import numpy as np
import pandas as pd
from copy import copy
from collections import defaultdict

from cascade_at.core.db import decomp_step as ds

from cascade_at.core.log import get_loggers
from cascade_at.inputs.base_input import BaseInput
from cascade_at.inputs.asdr import ASDR
from cascade_at.inputs.csmr import CSMR
from cascade_at.inputs.covariate_data import CovariateData
from cascade_at.inputs.covariate_specs import CovariateSpecs
from cascade_at.inputs.data import CrosswalkVersion
from cascade_at.inputs.demographics import Demographics
from cascade_at.inputs.locations import LocationDAG
from cascade_at.inputs.population import Population
from cascade_at.inputs.utilities.covariate_weighting import get_interpolated_covariate_values
from cascade_at.inputs.utilities.gbd_ids import get_location_set_version_id
from cascade_at.dismod.integrand_mappings import make_integrand_map
from cascade_at.inputs.utilities.transformations import COVARIATE_TRANSFORMS
from cascade_at.inputs.utilities.gbd_ids import SEX_ID_TO_NAME, SEX_NAME_TO_ID
from cascade_at.inputs.utilities.gbd_ids import CascadeConstants, StudyCovConstants
from cascade_at.inputs.utilities.covariate_weighting import expand_grid

LOG = get_loggers(__name__)


class MeasurementInputs:
    def __init__(self, model_version_id, gbd_round_id,
                 decomp_step_id, csmr_process_version_id,
                 csmr_cause_id, crosswalk_version_id,
                 country_covariate_id,
                 conn_def,
                 location_set_version_id=None,
                 drill=None):
        """
        The class that constructs all of the measurement inputs. Pulls ASDR, CSMR, crosswalk versions,
        and country covariates, and puts them into one data frame that then formats itself
        for the dismod database. Performs covariate value interpolation if age and year ranges
        don't match up with GBD age and year ranges.

        Parameters:
            model_version_id: (int) the model version ID
            gbd_round_id: (int) the GBD round ID
            decomp_step_id: (int) the decomp step ID
            csmr_process_version_id: (int) process version ID for CSMR
            csmr_cause_id: (int) cause to pull CSMR from
            crosswalk_version_id: (int) crosswalk version to use
            country_covariate_id: (list of int) list of covariate IDs
            conn_def: (str) connection definition from .odbc file (e.g. 'epi')
            location_set_version_id: (int) can be None, if it's none, get the
                best location_set_version_id for estimation hierarchy of this GBD round.
            drill: (int) optional, which location ID to drill from as the parent

        Attributes:
            self.decomp_step: (str) the decomp step in string form
            self.demographics: (cascade_at.inputs.demographics.Demographics) a demographics object
                that specifies the age group, sex, location, and year IDs to grab
            self.integrand_map: (dict) dictionary mapping from GBD measure IDs to DisMod IDs
            self.asdr: (cascade_at.inputs.asdr.ASDR) all-cause mortality input object
            self.csmr: (cascade_at.inputs.csmr.CSMR) cause-specific mortality input object from cause
                csmr_cause_id
            self.data: (cascade_at.inputs.data.CrosswalkVersion) crosswalk version data from IHME database
            self.covariate_data: (List[cascade_at.inputs.covariate_data.CovariateData]) list of covariate
                data objects that contains the raw covariate data mapped to IDs
            self.location_dag: (cascade_at.inputs.locations.LocationDAG) DAG of locations to be used
            self.population: (cascade_at.inputs.population.Population) population object that is used
                for covariate weighting
            self.data_eta: (Dict[str, float]): dictionary of eta value to be applied to each measure
            self.density: (Dict[str, str]): dictionary of density to be applied to each measure
            self.nu: (Dict[str, float]): dictionary of nu value to be applied to each measure
            self.dismod_data: (pd.DataFrame) resulting dismod data formatted to be used in the dismod database
        
        Usage:
        >>> from cascade_at.settings.base_case import BASE_CASE
        >>> from cascade_at.settings.settings import load_settings

        >>> settings = load_settings(BASE_CASE)
        >>> covariate_ids = [i.country_covariate_id for i in settings.country_covariate]

        >>> i = MeasurementInputs(model_version_id=settings.model.model_version_id,
        >>>            gbd_round_id=settings.gbd_round_id,
        >>>            decomp_step_id=settings.model.decomp_step_id,
        >>>            csmr_process_version_id=None,
        >>>            csmr_cause_id = settings.model.add_csmr_cause,
        >>>            crosswalk_version_id=settings.model.crosswalk_version_id,
        >>>            country_covariate_id=covariate_ids,
        >>>            conn_def='epi',
        >>>            location_set_version_id=settings.location_set_version_id)
        >>> i.get_raw_inputs()
        >>> i.configure_inputs_for_dismod()
        """
        LOG.info(f"Initializing input object for model version ID {model_version_id}.")
        LOG.info(f"GBD Round ID {gbd_round_id}.")
        LOG.info(f"Pulling from connection {conn_def}.")
        self.model_version_id = model_version_id
        self.gbd_round_id = gbd_round_id
        self.decomp_step_id = decomp_step_id
        self.csmr_process_version_id = csmr_process_version_id
        self.csmr_cause_id = csmr_cause_id
        self.crosswalk_version_id = crosswalk_version_id
        self.country_covariate_id = country_covariate_id
        self.conn_def = conn_def
        self.decomp_step = ds.decomp_step_from_decomp_step_id(
            self.decomp_step_id
        )
        self.demographics = Demographics(gbd_round_id=self.gbd_round_id)
        if location_set_version_id is None:
            self.location_set_version_id = get_location_set_version_id(
                gbd_round_id=self.gbd_round_id
            )
        else:
            self.location_set_version_id = location_set_version_id
        self.location_dag = LocationDAG(
            location_set_version_id=self.location_set_version_id,
            gbd_round_id=self.gbd_round_id
        )

        if drill:
            LOG.info(
                f"This is a DRILL model, so only going to pull data associated with "
                f"drill location start {drill} and its descendants."
            )
            drill_descendants = list(self.location_dag.descendants(location_id=drill))
            self.demographics.location_id = [drill] + drill_descendants

        self.integrand_map = make_integrand_map()

        self.exclude_outliers = True
        self.asdr = None
        self.csmr = None
        self.population = None
        self.data = None
        self.covariates = None
        self.age_groups = None

        self.data_eta = None
        self.density = None
        self.nu = None
        self.measures_to_exclude = None

        self.dismod_data = None
        self.covariate_data = None
        self.country_covariate_data = None
        self.covariate_specs = None
        self.omega = None

    def get_raw_inputs(self):
        """
        Get the raw inputs that need to be used
        in the modeling.

        :return:
        """
        LOG.info("Getting all raw inputs.")
        self.asdr = ASDR(
            demographics=self.demographics,
            decomp_step=self.decomp_step,
            gbd_round_id=self.gbd_round_id
        ).get_raw()
        self.csmr = CSMR(
            cause_id=self.csmr_cause_id,
            demographics=self.demographics,
            decomp_step=self.decomp_step,
            gbd_round_id=self.gbd_round_id,
            process_version_id=self.csmr_process_version_id
        ).get_raw()
        self.data = CrosswalkVersion(
            crosswalk_version_id=self.crosswalk_version_id,
            exclude_outliers=self.exclude_outliers,
            demographics=self.demographics,
            conn_def=self.conn_def,
            gbd_round_id=self.gbd_round_id
        ).get_raw()
        self.covariate_data = [CovariateData(
            covariate_id=c,
            demographics=self.demographics,
            decomp_step=self.decomp_step,
            gbd_round_id=self.gbd_round_id
        ).get_raw() for c in self.country_covariate_id]
        self.population = Population(
            demographics=self.demographics,
            decomp_step=self.decomp_step,
            gbd_round_id=self.gbd_round_id
        ).get_population()

    def configure_inputs_for_dismod(self, settings):
        """
        Modifies the inputs for DisMod based on model-specific settings.

        :param settings: (cascade.settings.configuration.Configuration)
        :return: self
        """
        self.data_eta = self.data_eta_from_settings(settings)
        self.density = self.density_from_settings(settings)
        self.nu = self.nu_from_settings(settings)
        self.measures_to_exclude = self.measures_to_exclude_from_settings(settings)

        # If we are constraining omega, then we want to hold out the data
        # from the DisMod fit for ASDR (but never CSMR -- always want to fit CSMR).
        data = self.data.configure_for_dismod(
            measures_to_exclude=self.measures_to_exclude,
            relabel_incidence=settings.model.relabel_incidence
        )
        asdr = self.asdr.configure_for_dismod(hold_out=settings.model.constrain_omega)
        csmr = self.csmr.configure_for_dismod(hold_out=0)

        self.dismod_data = pd.concat([data, asdr, csmr], axis=0)
        self.dismod_data.reset_index(drop=True, inplace=True)

        if settings.model.constrain_omega:
            self.omega = self.calculate_omega(asdr=asdr, csmr=csmr)
        else:
            self.omega = None

        self.dismod_data["density"] = self.dismod_data.measure.apply(self.density.__getitem__)
        self.dismod_data["eta"] = self.dismod_data.measure.apply(self.data_eta.__getitem__)
        self.dismod_data["nu"] = self.dismod_data.measure.apply(self.nu.__getitem__)

        # This makes the specs not just for the country covariate but adds on the
        # sex and one covariates.
        self.covariate_specs = CovariateSpecs(
            country_covariates=settings.country_covariate,
            study_covariates=settings.study_covariate
        )
        self.country_covariate_data = {c.covariate_id: c.configure_for_dismod(
            pop_df=self.population.configure_for_dismod(),
            loc_df=self.location_dag.df
        ) for c in self.covariate_data}

        self.dismod_data = self.add_covariates_to_data(df=self.dismod_data)
        self.dismod_data.loc[self.dismod_data.hold_out.isnull(), 'hold_out'] = 0.
        self.dismod_data.drop(['age_group_id'], inplace=True, axis=1)

        return self

    def add_covariates_to_data(self, df):
        """
        Add on covariates to a data frame that has age_group_id, year_id
        or time-age upper / lower, and location_id and sex_id. Adds both
        country-level and study-level covariates.
        :return:
        """
        cov_dict_for_interpolation = {
            c.name: self.country_covariate_data[c.covariate_id]
            for c in self.covariate_specs.covariate_specs
            if c.study_country == 'country'
        }

        df = self.interpolate_country_covariate_values(df=df, cov_dict=cov_dict_for_interpolation)
        df = self.transform_country_covariates(df=df)

        df['s_sex'] = df.sex_id.map(SEX_ID_TO_NAME).map(StudyCovConstants.SEX_COV_VALUE_MAP)
        df['s_one'] = StudyCovConstants.ONE_COV_VALUE

        return df

    def to_avgint(self, parent_location_id, sex_id):
        """
        Converts the demographics of the model to the avgint table.
        :return:
        """
        LOG.info(f"Getting grid for the avgint table "
                 f"for parent location ID {parent_location_id} "
                 f"and sex_id {sex_id}.")
        grid = expand_grid({
            'sex_id': [sex_id],
            'location_id': self.location_dag.parent_children(parent_location_id),
            'year_id': self.demographics.year_id,
            'age_group_id': self.demographics.age_group_id
        })
        grid['time_lower'] = grid['year_id'].astype(int)
        grid['time_upper'] = grid['year_id'] + 1.
        grid = BaseInput().convert_to_age_lower_upper(df=grid)

        LOG.info("Adding covariates to avgint grid.")
        grid = self.add_covariates_to_data(df=grid)
        return grid

    @staticmethod
    def calculate_omega(asdr, csmr):
        """
        Calculates other cause mortality (omega) from ASDR (mtall -- all-cause mortality)
        and CSMR (mtspecific -- cause-specific mortality). For most diseases, mtall is a
        good approximation to omega, but we calculate omega = mtall - mtspecific in case it isn't.
        For diseases without CSMR (self.csmr_cause_id = None), then omega = mtall.
        """
        join_columns = ['location_id', 'time_lower', 'time_upper',
                        'age_lower', 'age_upper', 'sex_id']
        mtall = asdr[join_columns + ['meas_value']].copy()
        mtall.rename(columns={'meas_value': 'mtall'}, inplace=True)

        if csmr.empty:
            omega = mtall.copy()
            omega.rename(columns={'mtall': 'omega'}, inplace=True)
        else:
            mtspecific = csmr[join_columns + ['meas_value']].copy()
            mtspecific.rename(columns={'meas_value': 'mtspecific'}, inplace=True)
            omega = mtall.merge(mtspecific, on=join_columns)
            omega['mean'] = omega['mtall'] - omega['mtspecific']
            omega.drop(columns=['mtall', 'mtspecific'], inplace=True)
        
        negative_omega = omega['mean'] < 0
        if any(negative_omega):
            raise ValueError("There are negative values for omega. Must fix.")

        return omega

    def interpolate_country_covariate_values(self, df, cov_dict):
        """
        Interpolates the covariate values onto the data
        so that the non-standard ages and years match up to meaningful
        covariate values.

        :param df: (pd.DataFrame)
        :param cov_dict: (Dict)
        """
        LOG.info(f"Interpolating and merging the country covariates.")
        interp_df = get_interpolated_covariate_values(
            data_df=df,
            covariate_dict=cov_dict,
            population_df=self.population.configure_for_dismod(),
            location_dag=self.location_dag
        )
        return interp_df

    def transform_country_covariates(self, df):
        """
        Transforms the covariate data with the transformation ID.
        :param df: (pd.DataFrame)
        :return: self
        """
        for c in self.covariate_specs.covariate_specs:
            if c.study_country == 'country':
                LOG.info(f"Transforming the data for country covariate {c.covariate_id}.")
                df[c.name] = df[c.name].apply(
                    lambda x: COVARIATE_TRANSFORMS[c.transformation_id](x)
                )
        return df

    def calculate_country_covariate_reference_values(self, parent_location_id, sex_id):
        """
        Gets the country covariate reference value for a covariate ID and a parent location ID.
        Also gets the maximum difference between the reference value and covariate values observed.

        Run this when you're going to make a DisMod AT database for a specific parent location
        and sex ID.

        :param: (int)
        :param parent_location_id: (int)
        :param sex_id: (int)
        :return: List[CovariateSpec] list of the covariate specs with the correct reference values and max diff.
        """
        covariate_specs = copy(self.covariate_specs)

        age_min = self.dismod_data.age_lower.min()
        age_max = self.dismod_data.age_upper.max()
        time_min = self.dismod_data.time_lower.min()
        time_max = self.dismod_data.time_upper.max()

        children = list(self.location_dag.dag.successors(parent_location_id))
        
        for c in covariate_specs.covariate_specs:
            if c.study_country == 'study':
                if c.name == 's_sex':
                    c.reference = StudyCovConstants.SEX_COV_VALUE_MAP[SEX_ID_TO_NAME[sex_id]]
                    c.max_difference = StudyCovConstants.MAX_DIFFERENCE_SEX_COV
                elif c.name == 's_one':
                    c.reference = StudyCovConstants.ONE_COV_VALUE
                    c.max_difference = StudyCovConstants.MAX_DIFFERENCE_ONE_COV
                else:
                    raise ValueError(f"The only two study covariates allowed are sex and one, you tried {c.name}.")
            elif c.study_country == 'country':
                LOG.info(f"Calculating the reference and max difference for country covariate {c.covariate_id}.")

                cov_df = self.country_covariate_data[c.covariate_id]
                parent_df = cov_df.loc[cov_df.location_id == parent_location_id].copy()
                child_df = cov_df.loc[cov_df.location_id.isin(children)].copy()
                all_loc_df = pd.concat([child_df, parent_df], axis=0)

                # if there is no data for the parent location at all (which there should be provided by Central Comp)
                # then we are going to set the reference value to 0.
                if cov_df.empty:
                    reference_value = 0
                    max_difference = np.nan
                else:
                    pop_df = self.population.configure_for_dismod()
                    pop_df = pop_df.loc[pop_df.location_id == parent_location_id].copy()

                    df_to_interp = pd.DataFrame({
                        'location_id': parent_location_id,
                        'sex_id': [SEX_NAME_TO_ID['Both']],
                        'age_lower': [age_min], 'age_upper': [age_max],
                        'time_lower': [time_min], 'time_upper': [time_max]
                    })
                    reference_value = get_interpolated_covariate_values(
                        data_df=df_to_interp,
                        covariate_dict={c.name: parent_df},
                        population_df=pop_df,
                        location_dag=self.location_dag
                    )[c.name].iloc[0]
                    max_difference = np.max(
                        np.abs(all_loc_df.mean_value - reference_value)
                    ) + CascadeConstants.PRECISION_FOR_REFERENCE_VALUES

                c.reference = reference_value
                c.max_difference = max_difference
        covariate_specs.create_covariate_list()
        return covariate_specs

    def measures_to_exclude_from_settings(self, settings):
        """
        Gets the measures to exclude from the data from the model
        settings configuration.
        :param settings: (cascade.settings.configuration.Configuration)
        :return:
        """
        if not settings.model.is_field_unset("exclude_data_for_param"):
            measures_to_exclude = [self.integrand_map[m].name
                                   for m in settings.model.exclude_data_for_param
                                   if m in self.integrand_map]
        else:
            measures_to_exclude = list()
        if settings.policies.exclude_relative_risk:
            measures_to_exclude.append("relrisk")
        return measures_to_exclude

    def data_eta_from_settings(self, settings):
        """
        Gets the data eta from the settings Configuration.
        The default data eta is np.nan.
        settings.eta.data: (Dict[str, float]): Default value for eta parameter on distributions
            as a dictionary from measure name to float

        :param settings: (cascade.settings.configuration.Configuration)
        :return:
        """
        data_eta = defaultdict(lambda: np.nan)
        if not settings.eta.is_field_unset("data") and settings.eta.data:
            data_eta = defaultdict(lambda: float(settings.eta.data))
        for set_eta in settings.data_eta_by_integrand:
            data_eta[self.integrand_map[set_eta.integrand_measure_id]] = float(set_eta.value)
        return data_eta

    def density_from_settings(self, settings):
        """
        Gets the density from the settings Configuration.
        The default density is "gaussian".
        settings.model.data_density: (Dict[str, float]): Default values for density parameter on distributions
            as a dictionary from measure name to string

        :param settings: (cascade.settings.configuration.Configuration)
        :return:
        """
        density = defaultdict(lambda: "gaussian")
        if not settings.model.is_field_unset("data_density") and settings.model.data_density:
            density = defaultdict(lambda: settings.model.data_density)
        for set_density in settings.data_density_by_integrand:
            density[self.integrand_map[set_density.integrand_measure_id]] = set_density.value
        return density

    @staticmethod
    def nu_from_settings(settings):
        """
        Gets nu from the settings Configuration.
        The default nu is np.nan.
        settings.students_dof.data: (Dict[str, float]): The parameter for students-t distributions
        settings.log_students_dof.data: (Dict[str, float]): The parameter for students-t distributions in log-space

        :param settings: (cascade.settings.configuration.Configuration)
        :return:
        """
        nu = defaultdict(lambda: np.nan)
        nu["students"] = settings.students_dof.data
        nu["log_students"] = settings.log_students_dof.data
        return nu

    def reset_index(self, drop, inplace):
        pass


class MeasurementInputsFromSettings(MeasurementInputs):
    def __init__(self, settings):
        """
        Wrapper for MeasurementInputs that takes a settings object rather than the
        individual arguments. For convenience.
        :param settings: (cascade.collector.settings_configuration.SettingsConfiguration)

        Example:
        >>> from cascade_at.settings.base_case import BASE_CASE
        >>> from cascade_at.settings.settings import load_settings

        >>> settings = load_settings(BASE_CASE)
        >>> i = MeasurementInputs(settings)
        >>> i.get_raw_inputs()
        >>> i.configure_inputs_for_dismod()
        """
        covariate_ids = [i.country_covariate_id for i in settings.country_covariate]

        if settings.model.drill:
            drill = settings.model.drill_location_start
        else:
            drill = None
        
        super().__init__(
            model_version_id=settings.model.model_version_id,
            gbd_round_id=settings.gbd_round_id,
            decomp_step_id=settings.model.decomp_step_id,
            csmr_process_version_id=None,
            csmr_cause_id=settings.model.add_csmr_cause,
            crosswalk_version_id=settings.model.crosswalk_version_id,
            country_covariate_id=covariate_ids,
            conn_def='epi',
            location_set_version_id=settings.location_set_version_id,
            drill=drill
        )
