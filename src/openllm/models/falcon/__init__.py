# Copyright 2023 BentoML Team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import typing as t

import openllm

_import_structure = {
    "configuration_falcon": ["FalconConfig", "START_FALCON_COMMAND_DOCSTRING", "DEFAULT_PROMPT_TEMPLATE"],
}

try:
    if not openllm.utils.is_torch_available():
        raise openllm.exceptions.MissingDependencyError
except openllm.exceptions.MissingDependencyError:
    pass
else:
    _import_structure["modeling_falcon"] = ["Falcon"]

if t.TYPE_CHECKING:
    from .configuration_falcon import \
        DEFAULT_PROMPT_TEMPLATE as DEFAULT_PROMPT_TEMPLATE
    from .configuration_falcon import \
        START_FALCON_COMMAND_DOCSTRING as START_FALCON_COMMAND_DOCSTRING
    from .configuration_falcon import FalconConfig as FalconConfig

    try:
        if not openllm.utils.is_torch_available():
            raise openllm.exceptions.MissingDependencyError
    except openllm.exceptions.MissingDependencyError:
        pass
    else:
        from .modeling_falcon import Falcon as Falcon
else:
    import sys

    sys.modules[__name__] = openllm.utils.LazyModule(
        __name__, globals()["__file__"], _import_structure, module_spec=__spec__
    )
