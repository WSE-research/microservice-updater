"""The custom exceptions carry human-readable messages used in HTTP responses."""
from tasks.exceptions import (
    InvalidVolumeMappingException,
    RepositoryAlreadyExistsException,
)


def test_repository_already_exists_is_exception():
    assert issubclass(RepositoryAlreadyExistsException, Exception)


def test_invalid_volume_mapping_carries_message():
    exc = InvalidVolumeMappingException("bad mapping")
    assert exc.message == "bad mapping"
