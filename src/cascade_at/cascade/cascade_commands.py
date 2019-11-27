"""
Sequences of cascade operations that work together to create a cascade command
that will run the whole cascade (or a drill -- which is a version of the cascade).
"""
from cascade_at.core.log import get_loggers
from cascade_at.cascade.cascade_operations import CASCADE_OPERATIONS

LOG = get_loggers(__name__)


class CascadeCommand:
    def __init__(self):
        self.task_list = []

    def get_commands(self):
        """
        Gets a list of commands in sequence so that you can run
        them without using jobmon.
        :return:
        """
        return [t.command for t in self.task_list]


class Drill(CascadeCommand):
    """
    Runs a drill!
    """
    def __init__(self, model_version_id, conn_def,
                 drill_location_start, drill_location_end, drill_sex):
        super().__init__()
        self.model_version_id = model_version_id
        self.conn_def = conn_def
        self.drill_location_start = drill_location_start
        self.drill_location_end = drill_location_end
        self.drill_sex = drill_sex

        self.task_list.append(CASCADE_OPERATIONS['configure_inputs'](
            model_version_id=self.model_version_id,
            conn_def=self.conn_def
        ))
        self.task_list.append(CASCADE_OPERATIONS['fit_both'](
            model_version_id=self.model_version_id,
            parent_location_id=self.drill_location_start,
            sex_id=self.drill_sex
        ))
        self.task_list.append(CASCADE_OPERATIONS['cleanup'](
            model_version_id=self.model_version_id
        ))


class TraditionalCascade(CascadeCommand):
    """
    Runs the traditional cascade.
    """
    def __init__(self, model_version_id):
        super().__init__()
        self.model_version_id = model_version_id
        raise NotImplementedError("Cascade is not implemented yet for Cascade-AT.")


CASCADE_COMMANDS = {
    'drill': Drill,
    'cascade': TraditionalCascade
}
