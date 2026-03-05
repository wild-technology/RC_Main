#!/bin/bash
# Session start hook for RC_Main Claude Code Agent sessions
# Runs unit tests to ensure the codebase is healthy before starting work

echo "=== RC_Main Session Start ==="
echo "Running unit test suite..."

python -m pytest tests/ -v -m "not windows_only" --tb=short -q 2>&1 | tail -5

if [ $? -ne 0 ]; then
    echo "WARNING: Unit tests are failing. Review test output before proceeding."
else
    echo "All unit tests passed. Codebase is healthy."
fi

echo ""
echo "Environment:"
python --version 2>&1
echo "Working directory: $(pwd)"
echo "Git branch: $(git branch --show-current 2>/dev/null || echo 'not a git repo')"
echo "==========================="
