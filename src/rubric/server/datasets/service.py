from datetime import datetime
from typing import List, Optional

from fastapi import Depends
from rubric.server.commons.errors import EntityNotFoundError, ForbiddenOperationError

from .dao import DatasetsDAO, create_datasets_dao
from .model import (
    CreationDatasetRequest,
    ObservationDataset,
    ObservationDatasetDB,
    TaskType,
    UpdateDatasetRequest,
)


class DatasetsService:
    """Datasets service"""

    def __init__(self, dao: DatasetsDAO):
        self.__dao__ = dao

    def list(
        self, owners: List[str] = None, task_type: Optional[TaskType] = None
    ) -> List[ObservationDataset]:
        """
        List datasets for a list of owners and task types

        Parameters
        ----------
        owners:
            The owners list. Optional
        task_type:
            The task type: Optional

        Returns
        -------
            A list of datasets
        """
        return [
            ObservationDataset(**obs.dict())
            for obs in self.__dao__.list_datasets(owner_list=owners)
            if task_type is None or task_type == obs.task
        ]

    def create(
        self,
        dataset: CreationDatasetRequest,
        owner: Optional[str],
        task: TaskType,
    ) -> ObservationDataset:
        """
        Creates a datasets from given creation request

        Parameters
        ----------
        dataset:
            The dataset creation fields
        owner:
            The dataset owner. Optional
        task:
            The dataset task

        Returns
        -------
            The created dataset

        """
        date_now = datetime.utcnow()
        db_dataset = ObservationDatasetDB(
            **dataset.dict(by_alias=True),
            task=task,
            owner=owner,
            created_at=date_now,
            last_updated=date_now
        )
        created_dataset = self.__dao__.create_dataset(db_dataset)
        return ObservationDataset.parse_obj(created_dataset)

    def find_by_name(self, name: str, owner: Optional[str]) -> ObservationDataset:
        """
        Find a dataset by name

        Parameters
        ----------
        name:
            The dataset name
        owner:
            The dataset owner. Optional

        Returns
        -------
            - The found dataset
            - EntityNotFoundError if not found
            - ForbiddenOperationError if user cannot access the dataset

        """
        found = self.__dao__.find_by_name(name, owner=owner)
        if not found:
            raise EntityNotFoundError(name=name, type=ObservationDataset)

        if found.owner and owner and found.owner != owner:
            raise ForbiddenOperationError()

        return ObservationDataset(**found.dict()) if found else None

    def delete(self, name: str, owner: Optional[str]):
        """
        Deletes a dataset.

        Parameters
        ----------
        name:
            The dataset name
        owner:
            The dataset owner

        """
        found = self.__dao__.find_by_name(name, owner)
        if found:
            self.__dao__.delete_dataset(found)

    def update(
        self,
        name: str,
        owner: Optional[str],
        data: UpdateDatasetRequest,
    ) -> ObservationDataset:
        """
        Updates an existing dataset. Fields in update data are
        merged with store ones. Updates cannot remove data fields.

        Parameters
        ----------
        name:
            The dataset name
        owner:
            The dataset owner
        data:
            The update fields

        Returns
        -------
            The updated dataset
        """

        found = self.__dao__.find_by_name(name, owner=owner)
        if not found:
            raise EntityNotFoundError(name=name, type=ObservationDataset)
        if found.owner and owner and found.owner != owner:
            raise ForbiddenOperationError()

        data.tags = {**found.tags, **data.tags}
        data.metadata = {**found.metadata, **data.metadata}
        updated = found.copy(
            update={**data.dict(by_alias=True), "last_updated": datetime.utcnow()}
        )

        self.__dao__.update_dataset(updated)
        return ObservationDataset(**updated.dict(by_alias=True))

    def upsert(
        self,
        dataset: CreationDatasetRequest,
        owner: Optional[str],
        task: Optional[TaskType] = None,
    ) -> ObservationDataset:
        """
        Inserts or updates the dataset. Updates only affects to updatable fields

        Parameters
        ----------
        dataset:
            The dataset data
        owner:
            The dataset owner
        task:
            The dataset task type

        Returns
        -------

            The updated or created dataset

        """
        try:
            return self.update(
                name=dataset.name,
                owner=owner,
                data=UpdateDatasetRequest(tags=dataset.tags, metadata=dataset.metadata),
            )
        except EntityNotFoundError:
            return self.create(
                dataset=dataset,
                task=task or TaskType.text_classification,
                owner=owner,
            )


_instance: Optional[DatasetsService] = None


def create_dataset_service(
    dao: DatasetsDAO = Depends(create_datasets_dao),
) -> DatasetsService:
    """
    Creates an instance of service

    Parameters
    ----------
    dao:
        The datasets dao

    Returns
    -------
        An instance of dataset service

    """

    global _instance

    if not _instance:
        _instance = DatasetsService(dao)
    return _instance