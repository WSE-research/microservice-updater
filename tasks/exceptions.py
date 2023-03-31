class RepositoryAlreadyExistsException(Exception):
    """
    Raised, if tried to clone an already existing repository
    """


class InvalidVolumeMappingException(Exception):
    """
    Raised, if a provided Docker volume mapping is provided
    """
    def __init__(self, message):
        self.message = message
