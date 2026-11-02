"""Test the bypass_sensors option of the alarmo.arm service."""

from typing import Any

import pytest

from tests.helpers import (
    advance_time,
    cleanup_timers,
    assert_alarm_state,
    setup_alarmo_entry,
    patch_alarmo_integration_dependencies,
)
from tests.factories import AreaFactory, SensorFactory

ALARM = "alarm_control_panel.test_area_1"
DOOR = "binary_sensor.area_1_door"
WINDOW = "binary_sensor.area_1_window"
BEDROOM = "binary_sensor.area_1_bedroom"
EXIT_TIME = 10  # AreaFactory default for armed_away


def _sensors() -> list[dict[str, Any]]:
    return [
        SensorFactory.create_door_sensor(entity_id=DOOR, name="Door"),
        SensorFactory.create_window_sensor(
            entity_id=WINDOW, name="Window", use_exit_delay=False
        ),
        SensorFactory.create_window_sensor(
            entity_id=BEDROOM, name="Bedroom", use_exit_delay=False
        ),
    ]


def _setup(hass: Any, entry_id: str):
    return setup_alarmo_entry(
        hass,
        areas=[AreaFactory.create_area(area_id="area_1")],
        sensors=_sensors(),
        entry_id=entry_id,
    )


async def _arm(hass: Any, mode: str = "away", **data: Any) -> None:
    await hass.services.async_call(
        "alarmo",
        "arm",
        {"entity_id": ALARM, "code": "1234", "mode": mode, **data},
        blocking=True,
    )
    await hass.async_block_till_done()


def _bypassed(hass: Any, entity_id: str = ALARM) -> list[str]:
    return hass.states.get(entity_id).attributes.get("bypassed_sensors") or []


@pytest.mark.asyncio
async def test_closed_sensor_is_bypassed_until_disarm(
    hass: Any, enable_custom_integrations: Any
) -> None:
    """A closed sensor in bypass_sensors is ignored while armed, then restored."""
    storage, entry = _setup(hass, "test_bypass_closed")
    with patch_alarmo_integration_dependencies(storage):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        for sensor in (DOOR, WINDOW, BEDROOM):
            hass.states.async_set(sensor, "off")
        await hass.async_block_till_done()

        await _arm(hass, mode="home", bypass_sensors=[BEDROOM])
        assert_alarm_state(hass, ALARM, "armed_home")
        assert _bypassed(hass) == [BEDROOM]

        # opening the bypassed sensor does not trigger
        # (sensor events are handled on a timer: let it run before checking)
        hass.states.async_set(BEDROOM, "on")
        await hass.async_block_till_done()
        await advance_time(hass, 1)
        assert_alarm_state(hass, ALARM, "armed_home")

        # a sensor that was not bypassed still triggers
        hass.states.async_set(WINDOW, "on")
        await hass.async_block_till_done()
        await advance_time(hass, 1)
        assert hass.states.get(ALARM).state in ("pending", "triggered")

        await hass.services.async_call(
            "alarmo", "disarm", {"entity_id": ALARM, "code": "1234"}, blocking=True
        )
        await hass.async_block_till_done()
        assert_alarm_state(hass, ALARM, "disarmed")
        assert _bypassed(hass) == []

        # the next arming without bypass_sensors watches the bedroom again
        hass.states.async_set(WINDOW, "off")
        await hass.async_block_till_done()
        await _arm(hass, mode="home")
        assert_alarm_state(hass, ALARM, "disarmed")  # bedroom still open: blocked
        await cleanup_timers(hass)


@pytest.mark.asyncio
async def test_open_sensor_in_bypass_sensors_does_not_block(
    hass: Any, enable_custom_integrations: Any
) -> None:
    """Only the listed open sensors are bypassed, unlike force."""
    storage, entry = _setup(hass, "test_bypass_open")
    with patch_alarmo_integration_dependencies(storage):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        hass.states.async_set(DOOR, "off")
        hass.states.async_set(WINDOW, "on")
        hass.states.async_set(BEDROOM, "on")
        await hass.async_block_till_done()

        # bedroom is excluded but the window is still open: arming fails
        await _arm(hass, mode="home", bypass_sensors=[BEDROOM])
        assert_alarm_state(hass, ALARM, "disarmed")
        assert _bypassed(hass) == []
        assert WINDOW in (hass.states.get(ALARM).attributes.get("open_sensors") or {})

        await _arm(hass, mode="home", bypass_sensors=[BEDROOM, WINDOW])
        assert_alarm_state(hass, ALARM, "armed_home")
        assert sorted(_bypassed(hass)) == sorted([BEDROOM, WINDOW])
        await cleanup_timers(hass)


@pytest.mark.asyncio
async def test_bypass_survives_exit_delay(
    hass: Any, enable_custom_integrations: Any
) -> None:
    """The request is kept when the exit delay ends and arming is validated again."""
    storage, entry = _setup(hass, "test_bypass_exit_delay")
    with patch_alarmo_integration_dependencies(storage):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        hass.states.async_set(DOOR, "off")
        hass.states.async_set(WINDOW, "off")
        hass.states.async_set(BEDROOM, "on")
        await hass.async_block_till_done()

        await _arm(hass, mode="away", bypass_sensors=[BEDROOM])
        assert_alarm_state(hass, ALARM, "arming")
        await advance_time(hass, EXIT_TIME + 1)
        assert_alarm_state(hass, ALARM, "armed_away")
        assert _bypassed(hass) == [BEDROOM]

        hass.states.async_set(BEDROOM, "off")
        await hass.async_block_till_done()
        hass.states.async_set(BEDROOM, "on")
        await hass.async_block_till_done()
        assert_alarm_state(hass, ALARM, "armed_away")
        await cleanup_timers(hass)


@pytest.mark.asyncio
async def test_unknown_and_other_area_sensors_are_ignored(
    hass: Any, enable_custom_integrations: Any
) -> None:
    """Entities that are not Alarmo sensors of the area are not added."""
    other = "binary_sensor.area_2_window"
    storage, entry = setup_alarmo_entry(
        hass,
        areas=[
            AreaFactory.create_area(area_id="area_1"),
            AreaFactory.create_area(area_id="area_2", name="Test Area 2"),
        ],
        sensors=[
            *_sensors(),
            SensorFactory.create_window_sensor(entity_id=other, area="area_2"),
        ],
        entry_id="test_bypass_ignored",
    )
    with patch_alarmo_integration_dependencies(storage):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        for sensor in (DOOR, WINDOW, BEDROOM, other):
            hass.states.async_set(sensor, "off")
        await hass.async_block_till_done()

        await _arm(hass, mode="home", bypass_sensors=[other, "light.kitchen", BEDROOM])
        assert_alarm_state(hass, ALARM, "armed_home")
        assert _bypassed(hass) == [BEDROOM]
        await cleanup_timers(hass)


@pytest.mark.asyncio
async def test_master_forwards_bypass_sensors_to_areas(
    hass: Any, enable_custom_integrations: Any
) -> None:
    """Arming the master bypasses each sensor in its own area."""
    other = "binary_sensor.area_2_window"
    storage, entry = setup_alarmo_entry(
        hass,
        areas=[
            AreaFactory.create_area(area_id="area_1", modes=["armed_away"]),
            AreaFactory.create_area(
                area_id="area_2", name="Test Area 2", modes=["armed_away"]
            ),
        ],
        sensors=[
            *_sensors(),
            SensorFactory.create_window_sensor(
                entity_id=other, area="area_2", use_exit_delay=False
            ),
        ],
        entry_id="test_bypass_master",
        master_enabled=True,
    )
    with patch_alarmo_integration_dependencies(storage):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        for sensor in (DOOR, WINDOW):
            hass.states.async_set(sensor, "off")
        hass.states.async_set(BEDROOM, "on")
        hass.states.async_set(other, "on")
        await hass.async_block_till_done()

        await hass.services.async_call(
            "alarmo",
            "arm",
            {
                "entity_id": "alarm_control_panel.master",
                "code": "1234",
                "mode": "away",
                "bypass_sensors": [BEDROOM, other],
            },
            blocking=True,
        )
        await hass.async_block_till_done()
        await advance_time(hass, EXIT_TIME + 1)
        assert_alarm_state(hass, ALARM, "armed_away")
        assert_alarm_state(hass, "alarm_control_panel.test_area_2", "armed_away")
        assert _bypassed(hass) == [BEDROOM]
        assert _bypassed(hass, "alarm_control_panel.test_area_2") == [other]
        assert sorted(_bypassed(hass, "alarm_control_panel.master")) == sorted(
            [BEDROOM, other]
        )
        await cleanup_timers(hass)
