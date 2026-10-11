"""Adapter for bachata.folders table."""

from core.adapters.repository import BaseAdapter


class FoldersAdapter(BaseAdapter):

    TABLE = "bachata.folders"

    def list_root(self) -> list[dict]:
        return self.select(order_by="position, name", parent_id=None)

    def list_children(self, parent_id: int) -> list[dict]:
        return self.select(order_by="position, name", parent_id=parent_id)

    def create(self, name: str, parent_id: int | None = None) -> dict:
        data = {"name": name}
        if parent_id is not None:
            data["parent_id"] = parent_id
        return self.add(**data)

    def rename(self, folder_id: int, name: str) -> dict | None:
        return self.update(folder_id, name=name)

    def get_breadcrumbs(self, folder_id: int) -> list[dict]:
        sql = """
        WITH RECURSIVE crumbs AS (
            SELECT id, name, parent_id
              FROM bachata.folders
             WHERE id = %s
            UNION ALL
            SELECT f.id, f.name, f.parent_id
              FROM bachata.folders f
              JOIN crumbs c ON c.parent_id = f.id
        )
        SELECT id, name FROM crumbs ORDER BY id
        """
        rows = self.repo.execute(sql, [folder_id], fetch="all")
        return [{"id": r[0], "name": r[1]} for r in (rows or [])]
