import os
import requests
import pytest
import docker
from armory import paths
import logging
from docker.errors import ImageNotFound
import torch
import tensorflow as tf
from armory.data import datasets

logger = logging.getLogger(__name__)

REQUIRED_DOCKER_IMAGES = [
    "twosixarmory/armory-base:dev",
    "twosixarmory/armory-pytorch:dev",
    "twosixarmory/armory-tf2:dev",
]


@pytest.fixture()
def ensure_armory_dirs(request):
    """
    CI doesn't mount volumes
    """
    docker_paths = paths.DockerPaths()
    saved_model_dir = docker_paths.saved_model_dir
    pytorch_dir = docker_paths.pytorch_dir
    dataset_dir = docker_paths.dataset_dir
    output_dir = docker_paths.output_dir

    os.makedirs(saved_model_dir, exist_ok=True)
    os.makedirs(pytorch_dir, exist_ok=True)
    os.makedirs(dataset_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)


@pytest.fixture()
def scenario_configs():
    """Pointer to armory.scenario_configs file"""
    dirname = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "scenario_configs"
    )
    if not os.path.exists(dirname):
        raise Exception(
            "Something is wrong... Scenario Config Dir: {} does not exist".format(
                dirname
            )
        )
    return dirname


@pytest.fixture(scope="session")
def docker_client():
    try:
        client = docker.from_env()
        logger.info("Docker Client Established...")
    except Exception as e:
        logger.error("Docker Server is not running!!")
        raise e

    for img in REQUIRED_DOCKER_IMAGES:
        try:
            client.images.get(name=img)
        except ImageNotFound:
            logger.error("Could not find Image: {}".format(img))
            raise

    return client


def pytest_addoption(parser):
    parser.addoption(
        "--armory-mode",
        action="store",
        default="docker",
        choices=["native", "docker", "both"],
        help="Set Armory Mode [native|docker|both]",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "skip_test_if_armory_mode(amory-mode): this mark skips the tests for the given armory mode",
    )


def pytest_runtest_setup(item):

    # Setting up for `--armory-mode`
    parameters = [
        mark.args[0] for mark in item.iter_markers(name="skip_test_if_armory_mode")
    ]
    if parameters:
        if item.config.getoption("--armory-mode") in parameters:
            pytest.skip("Test skipped because armory-mode is {!r}".format(parameters))


@pytest.fixture()
def external_resources():

    try:
        requests.get("https://www.google.com/").status_code
    except Exception as e:
        logger.error("Cannot Reach External Resources")
        raise e


@pytest.fixture
def armory_dataset_dir():
    from armory import paths

    paths.set_mode("host")
    return paths.runtime_paths().dataset_dir


@pytest.fixture
def dataset_generator():
    def generator(
        name,
        batch_size,
        num_epochs,
        split,
        framework,
        dataset_dir,
        shuffle_files=False,
        preprocessing_fn=None,
        fit_preprocessing_fn=None,
    ):
        # Instance types based on framework
        instance_types = {
            "pytorch": torch.utils.data.DataLoader,
            "tf": (tf.compat.v2.data.Dataset, tf.compat.v1.data.Dataset),
        }

        ds = getattr(datasets, name)
        dataset = ds(
            split=split,
            batch_size=batch_size,
            epochs=num_epochs,
            dataset_dir=dataset_dir,
            framework=framework,
            shuffle_files=shuffle_files,
            preprocessing_fn=preprocessing_fn,
            fit_preprocessing_fn=fit_preprocessing_fn,
        )
        assert isinstance(dataset, instance_types[framework])
        return dataset

    return generator
