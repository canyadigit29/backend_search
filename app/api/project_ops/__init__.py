# package marker
from . import project
from . import session_log

def load_background_tasks():
    from . import background_tasks  # import inside a function to avoid circular import
    return background_tasks

# You can later call this function to load background_tasks when needed
