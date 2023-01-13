from pathlib import Path
import os
import random
import shutil
import string
import subprocess

from armory.logs import log
from armory.datasets import build, common, upload

# .config and metadata.json are hardcoded values for tfds v4.6.0
# https://github.com/tensorflow/datasets/blob/v4.6.0/tensorflow_datasets/core/dataset_builder.py#L1257-L1286
DEFAULT_CONFIG_DIR = ".config"
DEFAULT_CONFIG_FILE = "metadata.json"


def package(
    name,
    version: str = None,
    data_dir: str = None,
    overwrite: bool = False,
) -> str:
    """
    Package a built dataset as .tar.gz, return path
    """
    version, data_dir, built_data_dir, subdir, builder_configs = build.build_info(
        name, version=version, data_dir=data_dir
    )

    data_dir = Path(data_dir)

    if not builder_configs:
        tar_list = [str(Path(name) / version)]
    else:
        # including DEFAULT_CONFIG_DIR, which contains DEFAULT_CONFIG_FILE that tfds refers to for default_config_name of a given dataset
        tar_list = [
            str(Path(name) / config.name / version) for config in builder_configs
        ] + [str(Path(name) / DEFAULT_CONFIG_DIR)]

    for tar_path in tar_list:
        expected_dir = data_dir / tar_path
        if not expected_dir.is_dir():
            raise FileNotFoundError(f"Dataset {tar_path} not found at {expected_dir}")

    tar_full_filepath = common.get_cache_dataset_path(name, version)
    if tar_full_filepath.is_file():
        if overwrite:
            tar_full_filepath.unlink(missing_ok=True)
        else:
            raise FileExistsError(
                f"Dataset {name} cache file {tar_full_filepath} exists. Use overwrite=True"
            )

    cmd = ["tar", "cvzf", str(tar_full_filepath)] + tar_list

    log.info("Creating tarball (may take some time)...")
    log.info(f"Running {' '.join(cmd)}")
    completed_process = subprocess.run(cmd, cwd=data_dir)
    completed_process.check_returncode()
    return str(tar_full_filepath)


def update(name, version: str = None, data_dir: str = None, url=None):
    """
    Hash file and update cached datasets file
    """
    version, data_dir, built_data_dir, subdir, builder_configs = build.build_info(
        name, version=version, data_dir=data_dir
    )
    filepath = common.get_cache_dataset_path(name, version)
    if not filepath.is_file():
        raise FileNotFoundError(f"filepath '{filepath}' not found.")
    assert (name, version) == common.parse_cache_filename(filepath.name)

    # we will rely on subdir to check what gets extracted from a cached file in package.extract
    if not builder_configs:
        subdir = str(Path(name) / version)
    else:
        subdir = [
            str(Path(name) / config.name / version) for config in builder_configs
        ] + [str(Path(name) / DEFAULT_CONFIG_DIR)]
    file_size, file_sha256 = common.hash_file(filepath)

    common.update_cached_datasets(name, version, subdir, file_size, file_sha256, url)


def verify(name, data_dir: str = None):
    info = common.cached_datasets()[name]
    version = info["version"]

    filepath = common.get_cache_dataset_path(name, version, data_dir=data_dir)
    if not filepath.is_file():
        raise FileNotFoundError(f"filepath '{filepath}' for dataset {name} not found.")

    common.verify_hash(filepath, info["size"], info["sha256"])


def extract(name, data_dir: str = None, overwrite: bool = False):
    """
    Extract cached dataset into tmp file then merge into data_dir
    """
    info = common.cached_datasets()[name]
    version = info["version"]

    if data_dir is None:
        data_dir = common.get_root()
    cache_dir = common.get_cache_dir(data_dir)
    filepath = common.get_cache_dataset_path(name, version)
    if not filepath.is_file():
        raise FileNotFoundError(f"filepath '{filepath}' for dataset {name} not found.")
    expected_subdir_list = common.cached_datasets()[name]["subdir"]
    if isinstance(expected_subdir_list, str):
        expected_subdir_list = [expected_subdir_list]

    # expected_subdir format is one of three formats:
    # <name>/<version>
    # <name>/<config>/<version>
    # <name>/DEFAULT_CONFIG_DIR
    for expected_subdir in expected_subdir_list:
        target_data_dir = data_dir / expected_subdir
        if target_data_dir.exists() and not overwrite:
            raise ValueError(
                f"Target directory {target_data_dir} exists. Set overwrite=True to overwrite"
            )

    # Extract to tmp directory
    tmp_dir = Path(cache_dir) / (
        "tmp_" + "".join(random.choice(string.ascii_lowercase) for _ in range(16))
    )
    tmp_dir.mkdir()
    cmd = ["tar", "zxvf", str(filepath), "--directory", str(tmp_dir)]
    log.info(f"Running {' '.join(cmd)}")
    completed_process = subprocess.run(cmd)
    completed_process.check_returncode()

    tmp_dir_listdir = os.listdir(tmp_dir)
    # should have directory structure <tmp_dir>/<name>/*
    if len(tmp_dir_listdir) != 1:
        raise ValueError(f"{tmp_dir} has more than 1 directory inside")
    if name not in tmp_dir_listdir:
        raise ValueError(f"{name} does not match directory in {tmp_dir}")
    tmp_dir_name = tmp_dir / name

    # elements in tmp_dir_name_listdir should be one of three values: <version>, <config>, DEFAULT_CONFIG_DIR
    tmp_dir_name_listdir = os.listdir(tmp_dir_name)
    # elements in expected_subdir_parsed_list is one of three values: <version>, <config>, DEFAULT_CONFIG_DIR
    expected_subdir_parsed_list = [
        expected_subdir.split("/")[1] for expected_subdir in expected_subdir_list
    ]
    # check to see that set of expected subdirectories match set of extracted subdirectories
    if set(tmp_dir_name_listdir) != set(expected_subdir_parsed_list):
        raise ValueError(
            f"Directories in {tmp_dir_name_listdir} do not match {expected_subdir_parsed_list}"
        )

    # len(tmp_dir_name_listdir) > 1 means that subdirectories exist in a dataset
    # and tmp_subdir should be <config> or DEFAULT_CONFIG_DIR
    # else, tmp_subdir should be <version>
    if len(tmp_dir_name_listdir) > 1:
        for tmp_subdir in tmp_dir_name_listdir:
            if tmp_subdir == DEFAULT_CONFIG_DIR:
                # should have directory structure <tmp_dir>/<name>/DEFAULT_CONFIG_DIR/DEFAULT_CONFIG_FILE
                if DEFAULT_CONFIG_FILE not in os.listdir(tmp_dir_name / tmp_subdir):
                    raise ValueError(
                        f"{DEFAULT_CONFIG_DIR} not in directory {tmp_dir_name / tmp_subdir}"
                    )
            else:
                # should have directory structure <tmp_dir>/<name>/<tmp_subdir>/<version>
                if version not in os.listdir(tmp_dir_name / tmp_subdir):
                    raise ValueError(
                        f"{version} does not match directory in {tmp_dir_name / tmp_subdir}"
                    )
    else:
        # should have directory structure <tmp_dir>/<name>/<version>
        if version not in tmp_dir_name_listdir:
            raise ValueError(f"{version} does not match directory in {tmp_dir_name}")

    for expected_subdir in expected_subdir_list:
        source_data_dir = tmp_dir / expected_subdir
        if any(child.is_dir() for child in source_data_dir.iterdir()):
            raise ValueError(
                f"{source_data_dir} directory should not have subdirectories"
            )

        target_data_dir = data_dir / expected_subdir
        if target_data_dir.exists() and overwrite:
            shutil.rmtree(target_data_dir)
        os.makedirs(target_data_dir.parent, exist_ok=True)
        shutil.move(source_data_dir, target_data_dir)
    shutil.rmtree(tmp_dir)


def add_to_cache(
    name,
    version: str = None,
    data_dir: str = None,
    overwrite: bool = False,
    public: bool = False,
):
    """
    Convenience function for packaging, uploading, and adding to cache
    """
    package(
        name,
        version=version,
        data_dir=data_dir,
        overwrite=overwrite,
    )
    update(name, version=version, data_dir=data_dir)
    verify(name, data_dir=data_dir)
    upload.upload(name, public=public)
