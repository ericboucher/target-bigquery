#!/usr/bin/env python3

import argparse
import io
import json
import sys
import traceback

import singer

from target_bigquery.encoders import DecimalEncoder
from target_bigquery.process import process
from target_bigquery.utils import emit_state, ensure_dataset
from target_bigquery.state import State, LiteralState

logger = singer.get_logger()


def main():
    # parse command line arguments (e.g., target config file path, state, table config file path, process handler type)
    parser = argparse.ArgumentParser()  # argparse.ArgumentParser(parents=[tools.argparser])
    parser.add_argument("-c", "--config", help="Config file", required=True)
    parser.add_argument("-t", "--tables", help="Table configs file", required=False)
    parser.add_argument("-s", "--state", help="Initial state file", required=False)

    # set boolean CLI arg [--merge-state | --no-merge-state]
    # https://docs.python.org/3/library/argparse.html
    # https://stackoverflow.com/questions/15008758/parsing-boolean-values-with-argparse
    parser.add_argument('--merge-state', action=argparse.BooleanOptionalAction, required=False, default=True,
                        help="Do we want to merge state messages into one state file or do we want to just keep the latest state message? Default is true")

    parser.add_argument("-ph", "--processhandler",
                        help="Defines the loading process. Partial loads by default.",
                        required=False,
                        choices=["load-job", "partial-load-job", "bookmarks-partial-load-job"],
                        default="partial-load-job"
                        )

    flags = parser.parse_args()

    # read target-config file into a dict
    with open(flags.config) as f:
        config = json.load(f)

    merge_state = flags.merge_state

    # target tables config (e.g, partitioning and clustering)
    table_config = flags.tables or config.get("table_config")
    tables = {}
    if table_config:
        with open(table_config) as f:
            tables = json.load(f)

    # state
    state = None
    if flags.state is not None:
        with open(flags.state) as f:
            state = json.load(f)

    # determine replication method: append vs. truncate
    truncate = False
    if config.get("replication_method", "append").lower() == "truncate":
        truncate = True

    # arguments supplied in target config
    table_prefix = config.get("table_prefix", "")
    table_suffix = config.get("table_suffix", "")
    location = config.get("location", "US")
    validate_records = config.get("validate_records", True)
    add_metadata_columns = config.get("add_metadata_columns", True)
    project_id, dataset_id = config["project_id"], config["dataset_id"]

    table_configs = tables.get("streams", {})
    max_cache = 1024 * 1024 * config.get("max_cache", 50)  # this is needed for partial loads

    tap_stream = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8")

    client, dataset = ensure_dataset(project_id, dataset_id, location)

    try:
        from target_bigquery.processhandler import LoadJobProcessHandler, PartialLoadJobProcessHandler, \
            BookmarksStatePartialLoadJobProcessHandler

        # determine type of process handler
        ph = None

        if flags.processhandler == "load-job":
            ph = LoadJobProcessHandler
        elif flags.processhandler == "partial-load-job":
            ph = PartialLoadJobProcessHandler
        elif flags.processhandler == "bookmarks-partial-load-job":
            ph = BookmarksStatePartialLoadJobProcessHandler
        else:
            raise Exception("Unknown process handler.")

        state_iterator = process(
            ph,
            tap_stream,
            initial_state=state,
            state_handler=State if merge_state else LiteralState,
            project_id=project_id,
            dataset=dataset,
            location=location,
            truncate=truncate,
            validate_records=validate_records,
            table_prefix=table_prefix,
            table_suffix=table_suffix,
            add_metadata_columns=add_metadata_columns,
            table_configs=table_configs,
            max_cache=max_cache
        )

        # write a state file
        for state in state_iterator:
            emit_state(state)

    except Exception as e:
        # load errors surface here
        logger.critical(e)
        exc_type, exc_value, exc_traceback = sys.exc_info()
        logger.critical(repr(traceback.format_exception(exc_type, exc_value, exc_traceback)))
        return 2  # sys.exit(2)

    return 0  # sys.exit(0)


if __name__ == "__main__":
    ret = main()
    sys.exit(ret)
