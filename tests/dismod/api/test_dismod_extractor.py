import pytest
from pathlib import Path
import pandas as pd
import numpy as np
import os

from cascade_at.dismod.api.run_dismod import run_dismod
from cascade_at.model.grid_alchemy import Alchemy
from cascade_at.dismod.api.dismod_filler import DismodFiller
from cascade_at.dismod.api.dismod_extractor import DismodExtractor


@pytest.fixture(scope='module')
def ex(mi, settings, temp_directory):
    alchemy = Alchemy(settings)
    print(temp_directory)
    d = DismodFiller(
        path=Path('extractor.db'),
        settings_configuration=settings,
        measurement_inputs=mi,
        grid_alchemy=alchemy,
        parent_location_id=70,
        sex_id=2
    )
    d.fill_for_parent_child()
    run = run_dismod(dm_file=str(d.path), command='init')
    if run.exit_status:
        print(run.stderr)
    assert run.exit_status == 0
    run = run_dismod(dm_file=str(d.path), command='fit fixed')
    if run.exit_status:
        print(run.stderr)
    assert run.exit_status == 0
    run = run_dismod(dm_file=str(d.path), command='predict fit_var')
    if run.exit_status:
        print(run.stderr)
    assert run.exit_status == 0
    return d    


def test_get_predictions(ex):
    d = DismodExtractor(path=Path('extractor.db'))
    pred = d.get_predictions()
    assert len(pred) == 33
    assert all(pred.columns == ['predict_id', 'sample_index', 'avgint_id', 'avg_integrand',
       'integrand_id', 'node_id', 'weight_id', 'subgroup_id', 'age_lower',
       'age_upper', 'time_lower', 'time_upper', 'c_age_group_id',
       'c_location_id', 'c_sex_id', 'c_year_id', 'x_0', 'x_1', 'x_2',
       'integrand_name', 'minimum_meas_cv', 'rate'])
    assert all(pred.age_lower == 0.0)
    assert all(pred.age_upper == 0.01917808)
    assert all(pred.integrand_id == np.repeat(list(range(0, 11)), 3))
    assert all(pred.time_lower == 1990.)
    assert all(pred.time_upper == 1991.)
    assert all(pred.c_age_group_id == 2)
    assert all(pred.c_sex_id == 2)


def test_format_for_ihme():
    d = DismodExtractor(path=Path('extractor.db'))
    pred = d.format_predictions_for_ihme()
    assert len(pred) == 36
    assert all(pred.columns == ['location_id', 'age_group_id', 'year_id', 'sex_id', 'measure_id',
       'mean', 'upper', 'lower'])
    assert all(pred.location_id == np.tile(list(range(70, 73)), 12))
    assert all(pred.sex_id == 2)
    assert all(pred.age_group_id == 2)
    assert all(pred.year_id == 1990)


def test_tearDown():
    os.remove('extractor.db')
