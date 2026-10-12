import sqlite3
import os
import sys

# Import necessary modules from the main application
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import Support.Header as init


def _resolve_db_path(db_path=None):
    """Resolve the main Neuron SQLite file (System/neuron/data/<hash>), never htdocs."""
    if db_path:
        return db_path
    try:
        from Support.Utils import get_main_db_path, load_database_name, ensure_sqlite_path
        ensure_sqlite_path()
        if not getattr(init, 'DATABASE_NAME', ''):
            load_database_name()
        path = get_main_db_path()
        if path and os.path.isfile(path):
            return path
    except Exception:
        pass
    # Fallback: PATH_SQLITE + DATABASE_NAME (never PATH_DATABASE/htdocs)
    name = getattr(init, 'DATABASE_NAME', '') or ''
    sqlite_dir = getattr(init, 'PATH_SQLITE', '') or ''
    if name and sqlite_dir:
        path = os.path.join(sqlite_dir, name)
        if os.path.isfile(path):
            return path
    return ''


def get_next_module_version(module_name, db_path=None):
    """
    Get the next version number for a module using automatic versioning (1.0, 1.1, 1.2...1.99, 2.0, etc.)

    Args:
        module_name: Name of the module
        db_path: Optional database path, defaults to main Neuron DB in PATH_SQLITE

    Returns:
        str: Next version number (e.g., "1.0", "1.1", "2.0")
    """
    db_path = _resolve_db_path(db_path)
    if not db_path:
        return "1.0"

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        cursor.execute(
            "SELECT version FROM Modules WHERE name = ? ORDER BY version DESC LIMIT 1",
            (module_name,),
        )
        result = cursor.fetchone()

        if result and result[0]:
            return increment_version(result[0])
        return "1.0"

    except sqlite3.OperationalError:
        return "1.0"
    finally:
        conn.close()


def increment_version(version_string):
    """
    Increment version number according to the specified pattern:
    1.0 -> 1.1 -> 1.2 -> ... -> 1.99 -> 2.0 -> 2.1 -> etc.
    """
    try:
        parts = version_string.split('.')
        if len(parts) != 2:
            return "1.0"

        major = int(parts[0])
        minor = int(parts[1])
        minor += 1
        if minor >= 100:
            major += 1
            minor = 0
        return f"{major}.{minor}"

    except (ValueError, IndexError):
        return "1.0"


def update_module_version(module_name, new_version, db_path=None):
    """
    Update a module's version in the database

    Returns:
        bool: True if a row was updated, False otherwise
    """
    db_path = _resolve_db_path(db_path)
    if not db_path:
        print(f"Error updating module version: main DB path not resolved for {module_name}")
        return False

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            UPDATE Modules
            SET version = ?
            WHERE name = ?
            """,
            (new_version, module_name),
        )
        conn.commit()
        return cursor.rowcount > 0

    except sqlite3.Error as e:
        print(f"Error updating module version: {e}")
        return False
    finally:
        conn.close()


def batch_increment_module_versions(module_names, db_path=None):
    """
    Increment versions for multiple modules and return the new versions

    Returns:
        dict: Dictionary mapping module names to their new versions
    """
    db_path = _resolve_db_path(db_path)
    module_versions = {}

    for module_name in module_names:
        new_version = get_next_module_version(module_name, db_path)
        module_versions[module_name] = new_version
        update_module_version(module_name, new_version, db_path)

    return module_versions


if __name__ == "__main__":
    print("Testing version increment system:")
    test_versions = ["1.0", "1.5", "1.99", "2.50", "invalid", ""]

    for version in test_versions:
        next_version = increment_version(version)
        print(f"{version} -> {next_version}")
