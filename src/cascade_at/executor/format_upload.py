import logging
from argparse import ArgumentParser

from cascade_at.context.model_context import Context
from cascade_at.dismod.api.dismod_filler import DismodIO
from cascade_at.core.log import get_loggers, LEVELS

LOG = get_loggers(__name__)


def get_args():
    """
    Parse the arguments for formatting and uploading results.
    :return:
    """
    parser = ArgumentParser()
    parser.add_argument("-model-version-id", type=int, required=True,
                        help="model version ID (need this from database entry)")
    parser.add_argument("--loglevel", type=str, required=False, default='info')

    return parser.parse_args()


def main():
    """
    Takes a dismod database that has had predict run on it and converts the predictions
    into the format needed for the IHME Epi Databases. Also uploads inputs to tier 3 which
    allows us to view those inputs in EpiViz.
    """
    args = get_args()
    logging.basicConfig(level=LEVELS[args.loglevel])

    context = Context(model_version_id=args.model_version_id)

    inputs, alchemy, settings = context.read_inputs()

    dismod_file = context.db_file(location_id=args.parent_location_id, sex_id=args.sex_id, make=False)
    da = DismodIO(path=dismod_file)