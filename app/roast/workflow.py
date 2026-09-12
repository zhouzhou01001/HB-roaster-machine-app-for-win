from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable

from app.roast.events import EVENT_TYPES, validate_event


class RoastStage(str, Enum):
    """The four document-defined stages for one continuous acquisition session."""

    IDLE = "idle"
    ROASTING = "roasting"
    AWAIT_END = "await_end"
    BETWEEN = "between"


class RoastFlowState(str, Enum):
    """Explicit operator workflow independent from legacy presentation stages."""

    IDLE = "idle"
    CONNECTED = "connected"
    READY_TO_CHARGE = "ready_to_charge"
    ROASTING = "roasting"
    FINISHED = "finished"


class WorkflowError(ValueError):
    """Raised when an operation is not legal in the current roast stage."""


@dataclass
class RoastSessionBuffer:
    """In-memory roast data. Nothing in this object writes to disk."""

    samples: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)

    def reset(self) -> None:
        self.samples.clear()
        self.events.clear()
        self.actions.clear()

    def snapshot(self) -> dict[str, list[dict[str, Any]]]:
        """Return a detached, serialisable snapshot for a read-only preview."""
        return {
            "samples": [dict(item) for item in self.samples],
            "events": [dict(item) for item in self.events],
            "actions": [dict(item) for item in self.actions],
        }


@dataclass
class RoastWorkflow:
    """Pure state machine for acquisition and multi-batch roast progression.

    The workflow deliberately owns no database, file, serial, or Qt object. UI code
    can therefore render a preview before selecting an explicit persistence action.
    """

    stage: RoastStage = RoastStage.IDLE
    state: RoastFlowState = RoastFlowState.IDLE
    batch_number: int = 0
    completed_batches: int = 0
    recorded_event_types: set[str] = field(default_factory=set)
    acquisition_suspended: bool = False

    @property
    def acquisition_active(self) -> bool:
        return self.state is not RoastFlowState.IDLE and not self.acquisition_suspended

    @property
    def roast_active(self) -> bool:
        return self.state is RoastFlowState.ROASTING

    @property
    def events_locked(self) -> bool:
        return self.state is RoastFlowState.FINISHED

    @property
    def can_finish(self) -> bool:
        return self.state is RoastFlowState.FINISHED

    @property
    def can_start_next_batch(self) -> bool:
        return self.state is RoastFlowState.CONNECTED

    @property
    def can_stop_acquisition(self) -> bool:
        return self.state in (RoastFlowState.IDLE, RoastFlowState.CONNECTED)

    def start_acquisition(self) -> int:
        if self.state is not RoastFlowState.IDLE:
            raise WorkflowError("采集已经启动，不能重复开始采集")
        self.stage = RoastStage.ROASTING
        self.acquisition_suspended = False
        self.state = RoastFlowState.CONNECTED
        self.batch_number = 1
        self.completed_batches = 0
        self.recorded_event_types.clear()
        return self.batch_number

    def start_roast(self) -> int:
        if self.state is not RoastFlowState.CONNECTED or self.acquisition_suspended:
            raise WorkflowError("仅设备正常采集时可以开始烘焙")
        self.state = RoastFlowState.READY_TO_CHARGE
        self.stage = RoastStage.ROASTING
        self.batch_number = max(1, self.completed_batches + 1)
        self.recorded_event_types.clear()
        return self.batch_number

    def start_next_batch(self) -> int:
        if self.state is not RoastFlowState.CONNECTED:
            raise WorkflowError("仅锅次间隙可以开始下一锅")
        return self.start_roast()

    def record_event(self, event_type: str, time_s: float, stored_events: Iterable[dict[str, Any]]) -> str:
        event_type = str(event_type).strip().upper()
        if self.state is RoastFlowState.FINISHED:
            raise WorkflowError("本锅已 DROP，等待结束烘焙")
        if self.state is RoastFlowState.READY_TO_CHARGE:
            if event_type != "CHARGE":
                raise WorkflowError("烘焙准备中，请先入豆")
            time_s = 0.0
        elif self.state is not RoastFlowState.ROASTING:
            raise WorkflowError("非烘焙中，不可记录事件")
        elif event_type == "CHARGE":
            raise WorkflowError("入豆已经记录，不能重复入豆")
        if event_type not in EVENT_TYPES:
            raise WorkflowError(f"不支持的事件：{event_type}")
        if event_type in self.recorded_event_types:
            raise WorkflowError(f"事件已记录：{event_type}")
        try:
            validate_event(list(stored_events), event_type, time_s)
        except ValueError as exc:
            raise WorkflowError(str(exc)) from exc
        self.recorded_event_types.add(event_type)
        if event_type == "CHARGE":
            self.state = RoastFlowState.ROASTING
        if event_type == "DROP":
            self.state = RoastFlowState.FINISHED
            self.stage = RoastStage.AWAIT_END
        return event_type

    def cancel_event(self, event_type: str) -> str:
        event_type = str(event_type).strip().upper()
        if self.state is RoastFlowState.FINISHED:
            raise WorkflowError("本锅已 DROP，等待结束烘焙")
        if self.state is not RoastFlowState.ROASTING:
            raise WorkflowError("仅烘焙中可撤销")
        if event_type in ("CHARGE", "DROP"):
            raise WorkflowError("入豆和排豆记录后不可撤销")
        if event_type not in self.recorded_event_types:
            raise WorkflowError("无可撤销事件")
        self.recorded_event_types.remove(event_type)
        return event_type

    def finish_current_batch(self) -> int:
        if not self.can_finish:
            raise WorkflowError("当前没有可结束的烘焙锅次")
        completed = self.batch_number
        self.completed_batches = completed
        self.stage = RoastStage.BETWEEN
        self.state = RoastFlowState.CONNECTED
        return completed

    def cancel_roast_preparation(self) -> None:
        if self.state is not RoastFlowState.READY_TO_CHARGE:
            raise WorkflowError("当前不在等待入豆阶段")
        self.state = RoastFlowState.CONNECTED
        self.stage = RoastStage.BETWEEN if self.completed_batches else RoastStage.ROASTING
        self.batch_number = max(self.completed_batches, 1)
        self.recorded_event_types.clear()

    def discard_empty_batch(self, sample_count: int) -> None:
        if not self.can_finish or sample_count != 0:
            raise WorkflowError("只能取消已排豆且没有正式采样的空锅")
        self.state = RoastFlowState.CONNECTED
        self.stage = RoastStage.BETWEEN
        self.batch_number = max(1, self.completed_batches)
        self.recorded_event_types.clear()

    def stop_acquisition(self) -> None:
        if not self.can_stop_acquisition:
            raise WorkflowError("请先结束烘焙再停止采集")
        self.reset_acquisition()

    def suspend_acquisition(self) -> None:
        """Keep the batch and events while transport recovery is exhausted."""
        if self.state is not RoastFlowState.IDLE:
            self.acquisition_suspended = True

    def resume_acquisition(self) -> None:
        if not self.acquisition_suspended:
            raise WorkflowError("采集没有暂停")
        self.acquisition_suspended = False

    def reset_acquisition(self) -> None:
        """Explicit teardown for closing the application or opening history."""
        self.acquisition_suspended = False
        self.stage = RoastStage.IDLE
        self.state = RoastFlowState.IDLE
        self.batch_number = 0
        self.recorded_event_types.clear()

    def status(self) -> dict[str, Any]:
        return {
            "stage": self.state.value,
            "legacy_stage": self.stage.value,
            "batch_number": self.batch_number,
            "completed_batches": self.completed_batches,
            "acquisition_active": self.acquisition_active,
            "events_locked": self.events_locked,
        }
