"""Tests for the ProjectMemory module."""

import pytest
import tempfile
import time
from pathlib import Path

from zouwucode.project_memory import ProjectMemory


class TestProjectMemory:
    """Tests for persistent project memory."""

    @pytest.fixture
    def pm(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = ProjectMemory(Path(tmp))
            yield memory

    def test_set_and_get_state(self, pm):
        pm.set_state("description", "Test project")
        assert pm.get_state("description") == "Test project"
        assert pm.get_state("nonexistent", "default") == "default"

    def test_update_state(self, pm):
        pm.update_state({"key1": "val1", "key2": "val2"})
        assert pm.get_state("key1") == "val1"
        assert pm.get_state("key2") == "val2"

    def test_add_decision(self, pm):
        pm.add_decision("Use FastAPI", "Decided to use FastAPI for the backend", "architecture")
        decisions = pm.get_decisions()
        assert len(decisions) == 1
        assert decisions[0]["title"] == "Use FastAPI"
        assert decisions[0]["category"] == "architecture"

    def test_get_decisions_by_category(self, pm):
        pm.add_decision("Decision 1", "Detail 1", "arch")
        pm.add_decision("Decision 2", "Detail 2", "db")
        arch_decisions = pm.get_decisions(category="arch")
        assert len(arch_decisions) == 1
        db_decisions = pm.get_decisions(category="db")
        assert len(db_decisions) == 1

    def test_decisions_context(self, pm):
        pm.add_decision("Use Python", "Use Python 3.10+", "tech")
        ctx = pm.get_decisions_context(limit=5)
        assert "Use Python" in ctx
        assert "Previous Decisions" in ctx

    def test_add_and_get_summaries(self, pm):
        pm.add_summary("session-1", "Worked on feature X", 10)
        pm.add_summary("session-2", "Fixed bugs in Y", 5)
        summaries = pm.get_summaries(limit=2)
        assert len(summaries) == 2

    def test_summaries_context(self, pm):
        pm.add_summary("session-1", "Implemented core feature", 15)
        ctx = pm.get_summaries_context(limit=3)
        assert "Past Session Summaries" in ctx
        assert "Implemented core feature" in ctx

    def test_goal_management(self, pm):
        pm.set_goal("Build a web API")
        assert pm.get_active_goal() == "Build a web API"
        ctx = pm.get_goal_context()
        assert "Build a web API" in ctx

        pm.complete_goal("Build a web API")
        assert pm.get_active_goal() == ""

    def test_save_and_load_persistence(self, pm):
        pm.set_state("version", "1.0")
        pm.add_decision("Key decision", "Important detail", "arch")
        pm.save_all()

        # Create a new instance pointing to the same directory
        pm2 = ProjectMemory(pm._project_root)
        data = pm2.load_all()
        assert data["state"].get("version") == "1.0"
        assert len(data["decisions"]) >= 1
        assert data["decisions"][0]["title"] == "Key decision"

    def test_get_full_context(self, pm):
        pm.set_state("description", "Test project")
        pm.set_goal("Finish tests")
        pm.add_decision("Use pytest", "Use pytest for testing", "testing")
        pm.add_summary("session-1", "Initial setup", 3)

        ctx = pm.get_full_context()
        assert "Test project" in ctx
        assert "Finish tests" in ctx
        assert "Use pytest" in ctx
        assert "Initial setup" in ctx

    def test_snapshot_project(self, pm):
        # Create a few files in the temp dir
        (pm._project_root / "main.py").write_text("print('hello')")
        (pm._project_root / "src").mkdir()
        (pm._project_root / "src" / "utils.py").write_text("def util(): pass")

        snapshot = pm.snapshot_project()
        assert snapshot["file_count"] >= 2
        assert "main.py" in str(snapshot["key_files"])