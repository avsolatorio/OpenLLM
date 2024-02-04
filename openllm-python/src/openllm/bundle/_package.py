from __future__ import annotations
import importlib.metadata, logging, os, pathlib
import bentoml, orjson, openllm_core
from simple_di import Provide, inject
from bentoml._internal.bento.build_config import BentoBuildConfig, DockerOptions, ModelSpec, PythonOptions
from bentoml._internal.configuration.containers import BentoMLContainer
from openllm_core.utils import SHOW_CODEGEN, check_bool_env, pkg

logger = logging.getLogger(__name__)

OPENLLM_DEV_BUILD = 'OPENLLM_DEV_BUILD'
_service_file = pathlib.Path(os.path.abspath(__file__)).parent.parent / '_service.py'
_SERVICE_VARS = '''import orjson;model_id,model_tag,adapter_map,serialization,trust_remote_code,max_model_len,gpu_memory_utilization='{__model_id__}','{__model_tag__}',orjson.loads("""{__model_adapter_map__}"""),'{__model_serialization__}',{__model_trust_remote_code__},{__max_model_len__},{__gpu_memory_utilization__}'''


def build_editable(path, package='openllm'):
  if not check_bool_env(OPENLLM_DEV_BUILD, default=False):
    return None
  # We need to build the package in editable mode, so that we can import it
  # TODO: Upgrade to 1.0.3
  from build import ProjectBuilder
  from build.env import IsolatedEnvBuilder

  module_location = pkg.source_locations(package)
  if not module_location:
    raise RuntimeError('Could not find the source location of OpenLLM.')
  pyproject_path = pathlib.Path(module_location).parent.parent / 'pyproject.toml'
  if os.path.isfile(pyproject_path.__fspath__()):
    with IsolatedEnvBuilder() as env:
      builder = ProjectBuilder(pyproject_path.parent)
      builder.python_executable = env.executable
      builder.scripts_dir = env.scripts_dir
      env.install(builder.build_system_requires)
      return builder.build('wheel', path, config_settings={'--global-option': '--quiet'})
  raise RuntimeError('Please install OpenLLM from PyPI or built it from Git source.')


def construct_python_options(llm, llm_fs, extra_dependencies=None, adapter_map=None):
  from . import RefResolver

  packages = ['scipy', 'bentoml[tracing]>=1.1.11,<1.2', f'openllm[vllm]>={RefResolver.from_strategy("release").version}']  # apparently bnb misses this one
  if adapter_map is not None:
    packages += ['openllm[fine-tune]']
  if extra_dependencies is not None:
    packages += [f'openllm[{k}]' for k in extra_dependencies]
  if llm.config['requirements'] is not None:
    packages.extend(llm.config['requirements'])
  built_wheels = [build_editable(llm_fs.getsyspath('/'), p) for p in ('openllm_core', 'openllm_client', 'openllm')]
  return PythonOptions(
    packages=packages,
    wheels=[llm_fs.getsyspath(f"/{i.split('/')[-1]}") for i in built_wheels] if all(i for i in built_wheels) else None,
    lock_packages=False,
  )


def construct_docker_options(llm, _, quantize, adapter_map, dockerfile_template, serialisation):
  from openllm_cli.entrypoint import process_environ

  environ = process_environ(llm.config, llm.config['timeout'], 1.0, None, True, llm.model_id, None, llm._serialisation, llm, use_current_env=False)
  # XXX: We need to quote this so that the envvar in container recognize as valid json
  environ['OPENLLM_CONFIG'] = f"'{environ['OPENLLM_CONFIG']}'"
  environ.pop('BENTOML_HOME', None)  # NOTE: irrelevant in container
  environ['NVIDIA_DRIVER_CAPABILITIES'] = 'compute,utility'
  return DockerOptions(python_version='3.11', env=environ, dockerfile_template=dockerfile_template)


@inject
def create_bento(
  bento_tag,
  llm_fs,
  llm,  #
  quantize,
  dockerfile_template,  #
  adapter_map=None,
  extra_dependencies=None,
  serialisation=None,  #
  _bento_store=Provide[BentoMLContainer.bento_store],
  _model_store=Provide[BentoMLContainer.model_store],
):
  _serialisation = openllm_core.utils.first_not_none(serialisation, default=llm.config['serialisation'])
  labels = dict(llm.identifying_params)
  labels.update({
    '_type': llm.llm_type,
    '_framework': llm.__llm_backend__,
    'start_name': llm.config['start_name'],
    'base_name_or_path': llm.model_id,
    'bundler': 'openllm.bundle',
    **{f'{package.replace("-","_")}_version': importlib.metadata.version(package) for package in {'openllm', 'openllm-core', 'openllm-client'}},
  })
  if adapter_map:
    labels.update(adapter_map)

  logger.debug("Building Bento '%s' with model backend '%s'", bento_tag, llm.__llm_backend__)
  logger.debug('Generating service vars %s (dir=%s)', llm.model_id, llm_fs.getsyspath('/'))
  script = f"# fmt: off\n# GENERATED BY 'openllm build {llm.model_id}'. DO NOT EDIT\n" + _SERVICE_VARS.format(
    __model_id__=llm.model_id,
    __model_tag__=str(llm.tag),  #
    __model_adapter_map__=orjson.dumps(adapter_map).decode(),
    __model_serialization__=llm.config['serialisation'],  #
    __model_trust_remote_code__=str(llm.trust_remote_code),
    __max_model_len__=llm._max_model_len,
    __gpu_memory_utilization__=llm._gpu_memory_utilization,  #
  )
  if SHOW_CODEGEN:
    logger.info('Generated _service_vars.py:\n%s', script)
  llm_fs.writetext('_service_vars.py', script)
  with open(_service_file.__fspath__(), 'r') as f:
    service_src = f.read()
  llm_fs.writetext(llm.config['service_name'], service_src)
  return bentoml.Bento.create(
    version=bento_tag.version,
    build_ctx=llm_fs.getsyspath('/'),
    build_config=BentoBuildConfig(
      service=f"{llm.config['service_name']}:svc",
      name=bento_tag.name,
      labels=labels,
      models=[ModelSpec.from_item({'tag': str(llm.tag), 'alias': llm.tag.name})],
      description=f"OpenLLM service for {llm.config['start_name']}",
      include=list(llm_fs.walk.files()),
      exclude=['/venv', '/.venv', '__pycache__/', '*.py[cod]', '*$py.class'],
      python=construct_python_options(llm, llm_fs, extra_dependencies, adapter_map),
      docker=construct_docker_options(llm, llm_fs, quantize, adapter_map, dockerfile_template, _serialisation),
    ),
  ).save(bento_store=_bento_store, model_store=_model_store)
