import os
import subprocess
import pathlib


def get_oci_env_path():
    return os.environ.get("OCI_ENV_PATH", os.getcwd())


def read_env_file(path):
    """
    Read the contents of a .env file into a dictionary.
    """
    result = {}

    try:
        with open(path, "r") as f:
            for line in f:
                if not line.startswith("#") and "=" in line:
                    key, val = line.split("=", maxsplit=1)

                    result[key.strip("' \"")] = val.strip("' \"\n")

    except FileNotFoundError:
        print(f"No .compose.env file found in {path}.")
        exit(1)

    return result


def get_config():
    """
    Parse the .compose.env file and return any defaults that aren't set there.
    """
    path = get_oci_env_path()

    # These values shouldn't be edited by the user and have the highest precedence.
    constant_vals = {
        "OCI_ENV_DIRECTORY": path.strip(os.sep).split(os.sep)[-1],
        "COMPOSE_CONTEXT": path,
    }

    # default values
    config = {
        "DEV_SOURCE_PATH": "",
        "COMPOSE_PROFILE": "",
        "DJANGO_SUPERUSER_USERNAME": "admin",
        "DJANGO_SUPERUSER_PASSWORD": "password",
        "API_HOST": "localhost",
        "API_PORT": "5001",
        "API_PROTOCOL": "http",
        "COMPOSE_PROJECT_NAME": constant_vals["OCI_ENV_DIRECTORY"],
        "COMPOSE_BINARY": "podman",
        "API_CONTAINER": "pulp",
        "DB_CONTAINER": "pulp",
        "CONTENT_APP_CONTAINER": "pulp",
        "WORKER_CONTAINER": "pulp",
        "DEV_IMAGE_SUFFIX": "",
        "DEV_VOLUME_SUFFIX": "",
        "NGINX_PORT": "5001",
        "NGINX_SSL_PORT": "443",
    }

    user_preferences = read_env_file(os.path.join(path, ".compose.env"))

    # override any defaults that the user set.
    return {**config, **user_preferences, **constant_vals}


def parse_profiles(config):
    """
    Load the profiles defined in COMPOSE_PROFILE
    """
    profiles = config["COMPOSE_PROFILE"].split(":")
    path = get_oci_env_path()
    oci_dir = config["OCI_ENV_DIRECTORY"]
    compiled_path = os.path.join(path, ".compiled")

    pathlib.Path(compiled_path).mkdir(exist_ok=True)

    profile_paths = [
        {
            "path": os.path.join(path, "base"),
            "name": "base",
            "container_path": os.path.join(
                "/src",
                oci_dir,
                "base"
            )
        },
    ]

    # parse the profiles and ensure that all of them exist.
    for profile in profiles:
        if "/" in profile:
            plugin, name = profile.split("/", maxsplit=1)

            profile_path = os.path.abspath(
                os.path.join(path, "..", plugin, "profiles", name)
            )
        else:
            plugin = oci_dir
            name = profile
            profile_path = os.path.join(path, "profiles", name)

        if not os.path.isdir(profile_path):
            print(f"{profile} from COMPOSE_PROFILE does not exist at {profile_path}")
            exit(1)

        profile_paths.append({
            "path": profile_path,
            "name": profile,
            "container_path": os.path.join(
                "/src",
                plugin,
                "profiles",
                name,
            )
        })

    init_script = [
        "#!/bin/bash",
        "",
        "# AUTOGENERATED by oci-env",
        "# This script runs automatically when the container starts.",
        ""
    ]

    env_output = []

    compose_files = []

    # Compile the information in the compose profiles into .compiled.
    for profile in profile_paths:
        init_file = os.path.join(profile["path"], "init.sh")
        env_file = os.path.join(profile["path"], "pulp_config.env")
        compose_file = os.path.join(profile["path"], "compose.yaml")

        # Add any init scripts to .compiled/init.sh.
        if os.path.isfile(init_file):
            script_path = os.path.join(profile['container_path'], "init.sh")
            init_script.append(f"bash {script_path}")

        # Combine all of the pulp_config.env files into .compiled/combined.env. Format
        # all the {VAR} templates.
        try: 
            with open(env_file, "r") as f:
                for line in f:
                    try:
                        env_output.append(line.strip().format(**config))
                    except KeyError as e:
                        print(
                            f"{env_file} contains variable {e}, which is not "
                            "defined in your .compose.env. This value is required to "
                            "be set."
                        )
                        exit(1)
        except FileNotFoundError:
            pass

        # Copy any compose files into .compiled and format any variables in them.
        try: 
            with open(compose_file, "r") as f:
                data = f.read()

                try:
                    data = data.format(**config)
                except KeyError as e:
                    print(
                        f"{compose_file} contains variable {e}, which is not "
                        "defined in your .compose.env. This value is required to "
                        "be set."
                    )

                    exit(1)

                compose_file = profile["name"].replace("/", "_")
                compose_file = compose_file + "_compose.yaml"
                compose_file = os.path.join(compiled_path, compose_file)

                compose_files.append(compose_file)

                with open(compose_file, "w") as out_file:
                    out_file.write(data)

        except FileNotFoundError:
            pass

    with open(os.path.join(compiled_path, "init.sh"), "w") as f:
        f.write("\n".join(init_script))

    with open(os.path.join(compiled_path, "combined.env"), "w") as f:
        f.write("\n".join(env_output))

    return compose_files


def exit_if_failed(rc):
    if rc != 0:
        exit(rc)


class Compose:
    """
    This provides an interface to docker/podman compose for running compose commands
    and executing scripts inside running containers.
    """
    def __init__(self, is_verbose):
        self.path = get_oci_env_path()
        self.config = get_config()
        self.compose_files = parse_profiles(self.config)
        self.is_verbose = is_verbose

    def compose_command(self, cmd, interactive=False, pipe_output=False):
        """
        Run a docker-compose or podman-compose command.

        This sets the correct project name and loads up all the compose files, but
        takes in the rest of the arguments (exec, up, down, etc) from the user.
        """
        binary = [self.config["COMPOSE_BINARY"] + "-compose", "-p", self.config["COMPOSE_PROJECT_NAME"]]

        compose_files = []

        for f in self.compose_files:
            compose_files.append("-f")
            compose_files.append(f)

        cmd = binary + compose_files + cmd

        if self.is_verbose:
            print(f"Running command in container: {' '.join(cmd)}")

        if interactive:
            return subprocess.call(cmd)
        else:
            return subprocess.run(cmd, capture_output=pipe_output)

    def exec(self, args, service=None, interactive=False, pipe_output=False):
        """
        Execute a script in a running container using podman or docker.

        This uses podman or docker directly rather than attempting to use
        docker/podman-compose since the information returned from the process
        differs between podman-compose and docker-compose.
        """
        service = service or self.config["API_CONTAINER"]
        project_name = self.config["COMPOSE_PROJECT_NAME"]
        binary = self.config["COMPOSE_BINARY"]

        container = f"{project_name}_{service}_1"

        # docker fails on systems with no interactive CLI. This tells docker
        # to use a pseudo terminal when no CLI is available.
        if os.getenv("COMPOSE_INTERACTIVE_NO_CLI", "0") == "1":
            cmd = [binary, "exec", container] + args
        else:
            cmd = [binary, "exec", "-it", container] + args

        if self.is_verbose:
            print(f"Running command in container: {' '.join(cmd)}")

        if interactive:
            proc = subprocess.call(cmd)
        else:
            proc = subprocess.run(cmd, capture_output=pipe_output)
        return proc

    def get_dynaconf_variable(self, name):
        """
        Get the value of a configuration from dynaconf.
        """

        return self.exec_container_script("get_dynaconf_var.sh", args=[name], pipe_output=True).stdout.decode().strip()
    
    def exec_container_script(self, script, args=None, interactive=False, pipe_output=False):
        """
        Executes a script from the base/container_scripts/ directory in the container.
        """

        args = args or []
        script_path = f"/src/{self.config['OCI_ENV_DIRECTORY']}/base/container_scripts/{script}"
        cmd = ["bash", script_path] + args

        return self.exec(cmd, interactive=interactive, pipe_output=pipe_output)
