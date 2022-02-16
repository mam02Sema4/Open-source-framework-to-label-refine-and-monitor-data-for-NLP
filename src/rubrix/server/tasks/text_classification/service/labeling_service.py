from typing import Any, Dict, List, Optional, Tuple

from fastapi import Depends
from pydantic import BaseModel, Field

from rubrix.server.commons.errors import EntityAlreadyExistsError, EntityNotFoundError
from rubrix.server.commons.es_helpers import filters
from rubrix.server.commons.helpers import unflatten_dict
from rubrix.server.datasets.dao import DatasetsDAO
from rubrix.server.datasets.model import BaseDatasetDB

from ...commons import EsRecordDataFieldNames
from ...commons.dao.dao import DatasetRecordsDAO
from ...commons.dao.model import RecordSearch
from ...commons.metrics.model.base import ElasticsearchMetric
from ..api.model import LabelingRule, TextClassificationDatasetDB


class DatasetLabelingRulesMetric(ElasticsearchMetric):
    id: str = Field("dataset_labeling_rules", const=True)
    name: str = Field(
        "Computes overall metrics for defined rules in dataset", const=True
    )

    def aggregation_request(self, all_rules: List[LabelingRule]) -> Dict[str, Any]:
        rules_filters = [filters.text_query(rule.query) for rule in all_rules]
        return {
            self.id: {
                "filters": {
                    "filters": {
                        "covered_records": filters.boolean_filter(
                            should_filters=rules_filters, minimum_should_match=1
                        ),
                        "annotated_covered_records": filters.boolean_filter(
                            filter_query=filters.exists_field(
                                EsRecordDataFieldNames.annotated_as
                            ),
                            should_filters=rules_filters,
                            minimum_should_match=1,
                        ),
                    }
                }
            }
        }


class LabelingRulesMetric(ElasticsearchMetric):
    id: str = Field("labeling_rule", const=True)
    name: str = Field("Computes metrics for a labeling rule", const=True)

    def aggregation_request(
        self,
        rule_query: str,
        labels: Optional[List[str]],
    ) -> Dict[str, Any]:

        annotated_records_filter = filters.exists_field(
            EsRecordDataFieldNames.annotated_as
        )
        rule_query_filter = filters.text_query(rule_query)
        aggr_filters = {
            "covered_records": rule_query_filter,
            "annotated_covered_records": filters.boolean_filter(
                filter_query=annotated_records_filter,
                should_filters=[rule_query_filter],
            ),
        }

        if labels is not None:
            for label in labels:
                label = self._encode_label_name(label)
                rule_label_annotated_filter = filters.annotated_as([label])
                aggr_filters.update(
                    {
                        f"{label}.correct_records": filters.boolean_filter(
                            filter_query=annotated_records_filter,
                            should_filters=[
                                rule_query_filter,
                                rule_label_annotated_filter,
                            ],
                            minimum_should_match=2,
                        ),
                        f"{label}.incorrect_records": filters.boolean_filter(
                            filter_query=annotated_records_filter,
                            must_query=rule_query_filter,
                            must_not_query=rule_label_annotated_filter,
                        ),
                    }
                )

        return {self.id: {"filters": {"filters": aggr_filters}}}

    @staticmethod
    def _encode_label_name(label: str) -> str:
        return label.replace(".", "@@@")

    @staticmethod
    def _decode_label_name(label: str) -> str:
        return label.replace("@@@", ".")

    def aggregation_result(self, aggregation_result: Dict[str, Any]) -> Dict[str, Any]:
        if self.id in aggregation_result:
            aggregation_result = aggregation_result[self.id]

        aggregation_result = unflatten_dict(aggregation_result)
        results = {
            "covered_records": aggregation_result.pop("covered_records"),
            "annotated_covered_records": aggregation_result.pop(
                "annotated_covered_records"
            ),
        }

        all_correct = []
        all_incorrect = []
        all_precision = []
        for label, metrics in aggregation_result.items():
            correct = metrics.get("correct_records", 0)
            incorrect = metrics.get("incorrect_records", 0)
            annotated = correct + incorrect
            metrics["annotated"] = annotated
            if annotated > 0:
                precision = correct / annotated
                metrics["precision"] = precision
                all_precision.append(precision)

            all_correct.append(correct)
            all_incorrect.append(incorrect)
            results[self._decode_label_name(label)] = metrics

        results["correct_records"] = sum(all_correct)
        results["incorrect_records"] = sum(all_incorrect)
        if len(all_precision) > 0:
            results["precision"] = sum(all_precision) / len(all_precision)

        return results


class DatasetLabelingRulesSummary(BaseModel):
    covered_records: int
    annotated_covered_records: int


class LabelingRuleSummary(BaseModel):
    covered_records: int
    annotated_covered_records: int
    correct_records: int = Field(default=0)
    incorrect_records: int = Field(default=0)
    precision: Optional[float] = None


class LabelingService:

    _INSTANCE = None

    __rule_metrics__ = LabelingRulesMetric()
    __dataset_rules_metrics__ = DatasetLabelingRulesMetric()

    @classmethod
    def get_instance(
        cls,
        dao: DatasetsDAO = Depends(DatasetsDAO.get_instance),
        records: DatasetRecordsDAO = Depends(DatasetRecordsDAO.get_instance),
    ):
        if cls._INSTANCE is None:
            cls._INSTANCE = cls(dao, records)
        return cls._INSTANCE

    def __init__(self, dao: DatasetsDAO, records: DatasetRecordsDAO):
        self.__dao__ = dao
        self.__records__ = records

    def _find_text_classification_dataset(
        self, dataset: BaseDatasetDB
    ) -> TextClassificationDatasetDB:
        found_ds = self.__dao__.find_by_id(
            dataset.id, ds_class=TextClassificationDatasetDB
        )
        if found_ds is None:
            raise EntityNotFoundError(dataset.name, dataset.__class__)
        return found_ds

    def list_rules(self, dataset: BaseDatasetDB) -> List[LabelingRule]:
        """List a set of rules for a given dataset"""
        found_ds = self._find_text_classification_dataset(dataset)
        return found_ds.rules

    def delete_rule(self, dataset: BaseDatasetDB, rule_query: str):
        """Delete a rule from a dataset by its defined query string"""
        found_ds = self._find_text_classification_dataset(dataset)
        new_rules_set = [r for r in found_ds.rules if r.query != rule_query]
        if len(found_ds.rules) != new_rules_set:
            found_ds.rules = new_rules_set
            self.__dao__.update_dataset(found_ds)

    def add_rule(self, dataset: BaseDatasetDB, rule: LabelingRule) -> LabelingRule:
        """Adds a rule to a dataset"""
        found_ds = self._find_text_classification_dataset(dataset)
        for r in found_ds.rules:
            if r.query == rule.query:
                raise EntityAlreadyExistsError(rule.query, type=LabelingRule)
        found_ds.rules.append(rule)
        self.__dao__.update_dataset(found_ds)
        return rule

    def compute_rule_metrics(
        self,
        dataset: BaseDatasetDB,
        rule_query: str,
        labels: Optional[List[str]] = None,
    ) -> Tuple[int, int, LabelingRuleSummary]:
        """Computes metrics for given rule query and optional label against a set of rules"""

        annotated_records = self._count_annotated_records(dataset)
        results = self.__records__.search_records(
            dataset,
            size=0,
            search=RecordSearch(
                include_default_aggregations=False,
                aggregations=self.__rule_metrics__.aggregation_request(
                    rule_query=rule_query, labels=labels
                ),
            ),
        )

        rule_metrics_summary = self.__rule_metrics__.aggregation_result(
            results.aggregations
        )

        metrics = LabelingRuleSummary.parse_obj(rule_metrics_summary)

        return results.total, annotated_records, metrics

    def _count_annotated_records(self, dataset: BaseDatasetDB) -> int:
        results = self.__records__.search_records(
            dataset,
            size=0,
            search=RecordSearch(
                query=filters.exists_field(EsRecordDataFieldNames.annotated_as),
                include_default_aggregations=False,
            ),
        )
        return results.total

    def all_rules_metrics(
        self, dataset: BaseDatasetDB
    ) -> Tuple[int, int, DatasetLabelingRulesSummary]:
        rules = self.list_rules(dataset)
        annotated_records = self._count_annotated_records(dataset)
        results = self.__records__.search_records(
            dataset,
            size=0,
            search=RecordSearch(
                include_default_aggregations=False,
                aggregations=self.__dataset_rules_metrics__.aggregation_request(
                    all_rules=rules
                ),
            ),
        )

        rule_metrics_summary = self.__dataset_rules_metrics__.aggregation_result(
            results.aggregations
        )

        return (
            results.total,
            annotated_records,
            DatasetLabelingRulesSummary.parse_obj(rule_metrics_summary),
        )

    def find_rule_by_query(
        self, dataset: BaseDatasetDB, rule_query: str
    ) -> LabelingRule:
        rule_query = rule_query.strip()
        for rule in self.list_rules(dataset):
            if rule.query == rule_query:
                return rule
        raise EntityNotFoundError(rule_query, type=LabelingRule)

    def replace_rule(self, dataset: BaseDatasetDB, rule: LabelingRule):
        found_ds = self._find_text_classification_dataset(dataset)

        for idx, r in enumerate(found_ds.rules):
            if r.query == rule.query:
                found_ds.rules[idx] = rule
                break

        self.__dao__.update_dataset(found_ds)
