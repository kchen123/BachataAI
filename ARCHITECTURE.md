# Architecture: Domain / API / Adapter

Three-layer design borrowed from PaperApp.

```
App (Flask)
  └── API        — HTTP in, JSON out. Validates, delegates, returns.
       └── Domain    — Business logic. Pure Python, no Flask.
            └── Adapter   — One class per DB table. CRUD + queries.
                 └── Repository — Generic SQL engine + connection pool.
```

## Repository

Single class that wraps a pooled `psycopg2` connection. Provides generic `add`, `get`, `select`, `update`, `delete`, `execute` (raw SQL). Returns dicts. Request-scoped via `get_repo()` stored in Flask `g`.

SQL lives in `.sql` files loaded by `load_sql(folder, name)` with caching.

## Adapter

Extends `BaseAdapter`. Set `TABLE = "schema.table"` and get CRUD for free. Add domain-specific query methods that call `self.repo.execute(load_sql(...))`.

```python
class ThingAdapter(BaseAdapter):
    TABLE = "myschema.things"

    def list_active(self) -> list[dict]:
        return self.select(order_by="name", active=True)

    def do_something_custom(self, id: int) -> dict | None:
        return self.repo.execute(load_sql("things", "custom_query"), [id], fetch="one_dict")
```

All adapters re-exported from `adapters/__init__.py` for clean imports.

## Domain

Pure functions. No Flask, no HTTP. Creates adapters via `ThingAdapter(get_repo())`, runs business logic, returns data.

```python
def create_thing(name: str) -> dict:
    repo = get_repo()
    thing = ThingAdapter(repo).create(name=name)
    RelatedAdapter(repo).setup_defaults(thing["id"])
    return thing
```

## API

Flask Blueprints. Handles HTTP concerns (request parsing, status codes, auth). Calls domain functions, returns `jsonify(...)`.

```python
@bp.route("/things", methods=["POST"])
def create_thing_route():
    body = request.get_json() or {}
    name = body.get("name", "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    thing = create_thing(name)
    return jsonify(thing), 201
```

## Flow

```
HTTP request
  -> API route (validate input)
    -> Domain function (business logic)
      -> Adapter (DB query)
        -> Repository (execute SQL, return dict)
      <- Adapter returns dict
    <- Domain returns result
  <- API returns jsonify(result)
```

## Rules

- **API** never touches the DB directly. Always goes through Domain.
- **Domain** never imports Flask. Adapter + `get_repo()` only.
- **Adapter** never has business logic. Just maps to SQL.
- One `Repository` per request, shared across all adapters.
