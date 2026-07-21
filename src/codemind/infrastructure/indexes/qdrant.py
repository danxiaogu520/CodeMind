"""Qdrant vector index adapter."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import AsyncQdrantClient, models

from codemind.domain.models import EmbeddedChunk, Language, RetrievalFilters, SearchHit


class QdrantVectorIndex:
    def __init__(
        self,
        client: AsyncQdrantClient,
        *,
        dimensions: int,
        collection_name: str = "codemind_code_chunks",
    ) -> None:
        self._client = client
        self._dimensions = dimensions
        self._collection_name = collection_name

    async def ensure_collection(self) -> None:
        if not await self._client.collection_exists(self._collection_name):
            await self._client.create_collection(
                self._collection_name,
                vectors_config=models.VectorParams(
                    size=self._dimensions, distance=models.Distance.COSINE
                ),
            )

    async def replace_version(
        self, repository_id: str, version_id: str, chunks: Sequence[EmbeddedChunk]
    ) -> None:
        await self.ensure_collection()
        version_filter = models.Filter(
            must=[
                models.FieldCondition(key="version_id", match=models.MatchValue(value=version_id))
            ]
        )
        await self._client.delete(
            self._collection_name, models.FilterSelector(filter=version_filter), wait=True
        )
        for start in range(0, len(chunks), 128):
            batch = chunks[start : start + 128]
            points = [
                models.PointStruct(
                    id=str(uuid5(NAMESPACE_URL, f"{version_id}:{embedded.chunk.id}")),
                    vector=list(embedded.vector),
                    payload={
                        "chunk_id": embedded.chunk.id,
                        "repository_id": repository_id,
                        "version_id": version_id,
                        "path": embedded.chunk.path,
                        "language": embedded.chunk.language.value,
                        "symbol_name": embedded.chunk.symbol_name,
                        "start_line": embedded.chunk.start_line,
                        "end_line": embedded.chunk.end_line,
                        "content": embedded.chunk.content,
                    },
                )
                for embedded in batch
            ]
            if points:
                await self._client.upsert(self._collection_name, points=points, wait=True)

    async def build_incremental_version(
        self,
        repository_id: str,
        version_id: str,
        base_version_id: str,
        unchanged_paths: Sequence[str],
        changed_chunks: Sequence[EmbeddedChunk],
    ) -> None:
        await self.ensure_collection()
        await self.delete_version(version_id)
        unchanged = set(unchanged_paths)
        offset: Any = None
        cloned: list[models.PointStruct] = []
        base_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key="repository_id", match=models.MatchValue(value=repository_id)
                ),
                models.FieldCondition(
                    key="version_id", match=models.MatchValue(value=base_version_id)
                ),
            ]
        )
        while True:
            points, offset = await self._client.scroll(
                self._collection_name,
                scroll_filter=base_filter,
                limit=128,
                offset=offset,
                with_payload=True,
                with_vectors=True,
            )
            for point in points:
                payload: dict[str, Any] = dict(point.payload or {})
                if str(payload.get("path", "")) not in unchanged or point.vector is None:
                    continue
                payload["version_id"] = version_id
                chunk_id = str(payload.get("chunk_id", point.id))
                cloned.append(
                    models.PointStruct(
                        id=str(uuid5(NAMESPACE_URL, f"{version_id}:{chunk_id}")),
                        vector=cast(Any, point.vector),
                        payload=payload,
                    )
                )
            if offset is None:
                break
        changed = [
            models.PointStruct(
                id=str(uuid5(NAMESPACE_URL, f"{version_id}:{embedded.chunk.id}")),
                vector=list(embedded.vector),
                payload={
                    "chunk_id": embedded.chunk.id,
                    "repository_id": repository_id,
                    "version_id": version_id,
                    "path": embedded.chunk.path,
                    "language": embedded.chunk.language.value,
                    "symbol_name": embedded.chunk.symbol_name,
                    "start_line": embedded.chunk.start_line,
                    "end_line": embedded.chunk.end_line,
                    "content": embedded.chunk.content,
                },
            )
            for embedded in changed_chunks
        ]
        all_points = [*cloned, *changed]
        for start in range(0, len(all_points), 128):
            await self._client.upsert(
                self._collection_name, points=all_points[start : start + 128], wait=True
            )

    async def delete_version(self, version_id: str) -> None:
        await self.ensure_collection()
        await self._client.delete(
            self._collection_name,
            models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="version_id", match=models.MatchValue(value=version_id)
                        )
                    ]
                )
            ),
            wait=True,
        )

    async def search(
        self,
        repository_id: str,
        version_id: str,
        vector: Sequence[float],
        limit: int,
        filters: RetrievalFilters | None = None,
    ) -> list[SearchHit]:
        await self.ensure_collection()
        must: list[models.Condition] = [
            models.FieldCondition(
                key="repository_id", match=models.MatchValue(value=repository_id)
            ),
            models.FieldCondition(key="version_id", match=models.MatchValue(value=version_id)),
        ]
        if filters is not None and filters.languages:
            must.append(
                models.FieldCondition(
                    key="language",
                    match=models.MatchAny(any=[language.value for language in filters.languages]),
                )
            )
        query_filter = models.Filter(must=must)
        requested_limit = limit * 4 if filters is not None and filters.path_prefix else limit
        response = await self._client.query_points(
            self._collection_name,
            query=list(vector),
            query_filter=query_filter,
            limit=requested_limit,
            with_payload=True,
        )
        hits: list[SearchHit] = []
        for point in response.points:
            payload: dict[str, Any] = dict(point.payload or {})
            path = str(payload.get("path", ""))
            if (
                filters is not None
                and filters.path_prefix
                and not path.startswith(filters.path_prefix)
            ):
                continue
            hits.append(
                SearchHit(
                    chunk_id=str(payload.get("chunk_id", point.id)),
                    score=float(point.score),
                    path=path,
                    content=str(payload.get("content", "")),
                    start_line=int(payload.get("start_line", 1)),
                    end_line=int(payload.get("end_line", 1)),
                    symbol_name=(
                        str(payload["symbol_name"]) if payload.get("symbol_name") else None
                    ),
                    reasons=("dense",),
                    language=(
                        Language(str(payload["language"])) if payload.get("language") else None
                    ),
                )
            )
            if len(hits) >= limit:
                break
        return hits
