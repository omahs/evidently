import datetime
import typing
import uuid
from collections import Counter
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
from typing import Union

import pandas as pd
from plotly import graph_objs as go

from evidently._pydantic_compat import BaseModel
from evidently.model.widget import BaseWidgetInfo
from evidently.pydantic_utils import EvidentlyBaseModel
from evidently.renderers.html_widgets import CounterData
from evidently.renderers.html_widgets import counter
from evidently.renderers.html_widgets import plotly_figure
from evidently.test_suite import TestSuite
from evidently.tests.base_test import Test
from evidently.tests.base_test import TestStatus
from evidently.ui.dashboard.base import DashboardPanel
from evidently.ui.dashboard.base import ReportFilter
from evidently.ui.dashboard.base import assign_panel_id
from evidently.ui.dashboard.utils import TEST_COLORS
from evidently.ui.dashboard.utils import CounterAgg
from evidently.ui.dashboard.utils import TestSuitePanelType
from evidently.ui.dashboard.utils import _get_test_hover
from evidently.ui.dashboard.utils import getattr_nested
from evidently.ui.type_aliases import TestResultPoints

if typing.TYPE_CHECKING:
    from evidently.ui.base import DataStorage


class TestFilter(BaseModel):
    test_id: Optional[str] = None
    test_hash: Optional[int] = None
    test_args: Dict[str, Union[EvidentlyBaseModel, Any]] = {}

    def test_matched(self, test: Test) -> bool:
        if self.test_hash is not None and hash(test) == self.test_hash:
            return True
        if self.test_id is not None and self.test_id != test.get_id():
            return False
        for field, value in self.test_args.items():
            try:
                if getattr_nested(test, field.split(".")) != value:
                    return False
            except AttributeError:
                return False
        return True

    def get(self, test_suite: TestSuite) -> Dict[Test, TestStatus]:
        results = {}
        for test in test_suite._inner_suite.context.tests:
            if self.test_matched(test):
                try:
                    results[test] = test.get_result().status
                except AttributeError:
                    pass
        return results


class DashboardPanelTestSuite(DashboardPanel):
    test_filters: List[TestFilter] = []
    filter: ReportFilter = ReportFilter(metadata_values={}, tag_values=[], include_test_suites=True)
    panel_type: TestSuitePanelType = TestSuitePanelType.AGGREGATE
    time_agg: Optional[str] = None

    @assign_panel_id
    def build(
        self,
        data_storage: "DataStorage",
        project_id: uuid.UUID,
        timestamp_start: Optional[datetime.datetime],
        timestamp_end: Optional[datetime.datetime],
    ) -> BaseWidgetInfo:
        self.filter.include_test_suites = True
        points: TestResultPoints = data_storage.load_test_results(
            project_id, self.filter, self.test_filters, self.time_agg, timestamp_start, timestamp_end
        )

        if self.panel_type == TestSuitePanelType.AGGREGATE:
            fig = self._create_aggregate_fig(points)
        elif self.panel_type == TestSuitePanelType.DETAILED:
            fig = self._create_detailed_fig(points)
        else:
            raise ValueError(f"Unknown panel type {self.panel_type}")

        return plotly_figure(title=self.title, figure=fig, size=self.size)

    def _create_aggregate_fig(self, points: Dict[datetime.datetime, Dict[Test, TestStatus]]):
        dates = list(sorted(points.keys()))
        bars = [Counter(points[d].values()) for d in dates]
        fig = go.Figure(
            data=[
                go.Bar(name=status.value, x=dates, y=[c[status] for c in bars], marker_color=color)
                for status, color in TEST_COLORS.items()
            ],
            layout={"showlegend": True},
        )
        fig.update_layout(barmode="stack")
        return fig

    def _create_detailed_fig(self, points: Dict[datetime.datetime, Dict[Test, TestStatus]]):
        dates = list(sorted(points.keys()))
        tests = list(set(t for p in points.values() for t in p.keys()))
        fig = go.Figure(
            data=[
                go.Bar(
                    name=test.name,
                    x=dates,
                    y=[1 for _ in range(len(dates))],
                    marker_color=[TEST_COLORS.get(points[d].get(test, TestStatus.SKIPPED)) for d in dates],
                    hovertemplate=_get_test_hover(test),
                    showlegend=False,
                )
                for test in tests
            ]
            + [
                go.Scatter(
                    x=[None],
                    y=[None],
                    mode="markers",
                    name=status.value,
                    marker=dict(size=7, color=col, symbol="square"),
                )
                for status, col in TEST_COLORS.items()
            ],
            layout={"showlegend": True},
        )
        fig.update_layout(
            barmode="stack",
            bargap=0.01,
            barnorm="fraction",
        )
        return fig


def to_period(time_agg: Optional[str], timestamp: datetime.datetime) -> datetime.datetime:
    if time_agg is None:
        return timestamp
    return pd.Series([timestamp], name="dt").dt.to_period(time_agg)[0]


class DashboardPanelTestSuiteCounter(DashboardPanel):
    agg: CounterAgg = CounterAgg.NONE
    filter: ReportFilter = ReportFilter(metadata_values={}, tag_values=[], include_test_suites=True)
    test_filters: List[TestFilter] = []
    statuses: List[TestStatus] = [TestStatus.SUCCESS]

    @assign_panel_id
    def build(
        self,
        data_storage: "DataStorage",
        project_id: uuid.UUID,
        timestamp_start: Optional[datetime.datetime],
        timestamp_end: Optional[datetime.datetime],
    ) -> BaseWidgetInfo:
        if self.agg == CounterAgg.NONE:
            statuses, postfix = self._build_none(data_storage, project_id, timestamp_start, timestamp_end)
        elif self.agg == CounterAgg.LAST:
            statuses, postfix = self._build_last(data_storage, project_id, timestamp_start, timestamp_end)
        else:
            raise ValueError(f"TestSuite Counter does not support agg {self.agg}")

        total = sum(statuses.values())
        value = sum(statuses[s] for s in self.statuses)
        statuses_join = ", ".join(s.value for s in self.statuses)
        return counter(counters=[CounterData(f"{value}/{total} {statuses_join}{postfix}", self.title)], size=self.size)

    def _build_none(
        self,
        data_storage: "DataStorage",
        project_id: uuid.UUID,
        timestamp_start: Optional[datetime.datetime],
        timestamp_end: Optional[datetime.datetime],
    ) -> Tuple[Counter, str]:
        points = data_storage.load_test_results(
            project_id, self.filter, self.test_filters, None, timestamp_start, timestamp_end
        )
        statuses: typing.Counter[TestStatus] = Counter()
        for _, values in points.values():
            statuses.update(values)
        return statuses, ""

    def _build_last(
        self,
        data_storage: "DataStorage",
        project_id: uuid.UUID,
        timestamp_start: Optional[datetime.datetime],
        timestamp_end: Optional[datetime.datetime],
    ) -> Tuple[Counter, str]:
        points = data_storage.load_test_results(
            project_id, self.filter, self.test_filters, None, timestamp_start, timestamp_end
        )

        if len(points) == 0:
            return Counter(), "(no data)"
        last_ts = max(points.keys())
        statuses: typing.Counter[TestStatus] = Counter(points[last_ts].values())
        return statuses, f" ({last_ts})"
