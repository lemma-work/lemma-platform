def python_install_probe_command() -> str:
    """Install one offline wheel and exercise its module and console script."""
    return """\
python - <<'PY'
from pathlib import Path
import zipfile

wheel = Path("/workspace/agentbox_install_probe-0.0.1-py3-none-any.whl")
records = (
    "agentbox_install_probe.py,,\\n"
    "agentbox_install_probe-0.0.1.dist-info/METADATA,,\\n"
    "agentbox_install_probe-0.0.1.dist-info/WHEEL,,\\n"
    "agentbox_install_probe-0.0.1.dist-info/entry_points.txt,,\\n"
    "agentbox_install_probe-0.0.1.dist-info/RECORD,,\\n"
)
with zipfile.ZipFile(wheel, "w") as archive:
    archive.writestr(
        "agentbox_install_probe.py",
        "VALUE = 'shared-3.14'\\ndef main():\\n    print(VALUE)\\n",
    )
    archive.writestr(
        "agentbox_install_probe-0.0.1.dist-info/METADATA",
        "Metadata-Version: 2.1\\nName: agentbox-install-probe\\nVersion: 0.0.1\\n",
    )
    archive.writestr(
        "agentbox_install_probe-0.0.1.dist-info/WHEEL",
        "Wheel-Version: 1.0\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n",
    )
    archive.writestr(
        "agentbox_install_probe-0.0.1.dist-info/entry_points.txt",
        "[console_scripts]\\nagentbox-install-probe = agentbox_install_probe:main\\n",
    )
    archive.writestr("agentbox_install_probe-0.0.1.dist-info/RECORD", records)
PY
pip install --no-deps --force-reinstall \
    /workspace/agentbox_install_probe-0.0.1-py3-none-any.whl
agentbox-install-probe
python -c "import agentbox_install_probe; print(agentbox_install_probe.VALUE)"
"""
