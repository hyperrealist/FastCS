from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from fastcs.attribute_io import AttributeIO
from fastcs.attribute_io_ref import AttributeIORef
from fastcs.attributes import Attribute, AttrR, AttrRW, AttrW
from fastcs.connections import IPConnection, IPConnectionSettings
from fastcs.controller import Controller, SubController
from fastcs.datatypes import Bool, DataType, Float, Int, String, T
from fastcs.launch import FastCS
from fastcs.transport.epics.ca.options import EpicsCAOptions
from fastcs.transport.epics.options import EpicsIOCOptions


class TemperatureControllerParameter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str
    type: Literal["bool", "int", "float", "str"]
    access_mode: Literal["r", "rw"]

    @property
    def fastcs_datatype(self) -> DataType:
        match self.type:
            case "bool":
                return Bool()
            case "int":
                return Int()
            case "float":
                return Float()
            case "str":
                return String()


def create_attributes(parameters: dict[str, Any]) -> dict[str, Attribute]:
    attributes: dict[str, Attribute] = {}
    for name, parameter in parameters.items():
        name = name.replace(" ", "_").lower()

        try:
            parameter = TemperatureControllerParameter.model_validate(parameter)
        except ValidationError as e:
            print(f"Failed to validate parameter '{parameter}'\n{e}")
            continue

        io_ref = TemperatureControllerAttributeIORef(command_name=parameter.command)
        match parameter.access_mode:
            case "r":
                attributes[name] = AttrR(parameter.fastcs_datatype, io_ref=io_ref)
            case "rw":
                attributes[name] = AttrRW(parameter.fastcs_datatype, io_ref=io_ref)

    return attributes


@dataclass(kw_only=True)
class TemperatureControllerAttributeIORef(AttributeIORef):
    command_name: str
    update_period: float | None = 0.2


class TemperatureControllerAttributeIO(
    AttributeIO[T, TemperatureControllerAttributeIORef]
):
    def __init__(self, connection: IPConnection):
        self._connection = connection

    async def update(self, attr: AttrR):
        response = await self._connection.send_query(f"{attr.io_ref.command_name}?\r\n")
        value = response.strip("\r\n")

        await attr.set(attr.dtype(value))

    async def put(self, attr: AttrW, value: Any):
        await self._connection.send_command(f"{attr.io_ref.command_name}={value}\r\n")


class TemperatureRampController(SubController):
    def __init__(self, index: int, io: AttributeIO):
        super().__init__(f"Ramp {index}", ios=[io])

    async def initialise(self, parameters: dict[str, Any]):
        self.attributes.update(create_attributes(parameters))


class TemperatureController(Controller):
    def __init__(self, settings: IPConnectionSettings):
        self._ip_settings = settings
        self.connection = IPConnection()
        self._temperature_io = TemperatureControllerAttributeIO(self.connection)
        super().__init__(ios=[self._temperature_io])

    async def connect(self):
        await self.connection.connect(self._ip_settings)

    async def initialise(self):
        await self.connect()

        api = json.loads((await self.connection.send_query("API?\r\n")).strip("\r\n"))

        ramps_api = api.pop("Ramps")
        self.attributes.update(create_attributes(api))

        for idx, ramp_parameters in enumerate(ramps_api):
            ramp_controller = TemperatureRampController(idx + 1, self._temperature_io)
            self.register_sub_controller(f"Ramp{idx + 1:02d}", ramp_controller)
            await ramp_controller.initialise(ramp_parameters)

        await self.connection.close()


epics_options = EpicsCAOptions(ca_ioc=EpicsIOCOptions(pv_prefix="DEMO"))
connection_settings = IPConnectionSettings("localhost", 25565)
fastcs = FastCS(TemperatureController(connection_settings), [epics_options])

# fastcs.run()  # Commented as this will block
