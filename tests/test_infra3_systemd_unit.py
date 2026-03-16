"""Test for systemd service unit file."""
import os
import re

SERVICE_FILE = "ui/autodev-ui.service"


def test_service_file_exists():
    """Service file must exist."""
    assert os.path.exists(SERVICE_FILE), f"{SERVICE_FILE} not found"


def test_valid_systemd_unit():
    """File must parse as valid systemd unit."""
    with open(SERVICE_FILE) as f:
        content = f.read()
    
    # Check required sections exist
    assert "[Unit]" in content, "Missing [Unit] section"
    assert "[Service]" in content, "Missing [Service] section"
    assert "[Install]" in content, "Missing [Install] section"


def test_unit_section_contains_description_and_after():
    """Unit section must contain Description and After=network.target."""
    with open(SERVICE_FILE) as f:
        content = f.read()
    
    assert re.search(r"Description\s*=", content), "Missing Description in [Unit]"
    assert "After=network.target" in content, "Missing After=network.target in [Unit]"


def test_service_section_exec_start():
    """Service section must contain ExecStart with correct python path."""
    with open(SERVICE_FILE) as f:
        content = f.read()
    
    # Extract [Service] section
    service_match = re.search(r"\[Service\](.*?)(?:\[|$)", content, re.DOTALL)
    assert service_match, "Could not find [Service] section"
    service_content = service_match.group(1)
    
    assert "/usr/bin/python3" in service_content, "ExecStart must use /usr/bin/python3"
    assert "ui/server.py" in service_content, "ExecStart must reference ui/server.py"


def test_service_section_working_directory():
    """Service section must contain WorkingDirectory with placeholder comment."""
    with open(SERVICE_FILE) as f:
        content = f.read()
    
    service_match = re.search(r"\[Service\](.*?)(?:\[|$)", content, re.DOTALL)
    service_content = service_match.group(1)
    
    assert "WorkingDirectory=" in service_content, "Missing WorkingDirectory in [Service]"
    # Check for placeholder comment
    assert "placeholder" in content.lower() or "EDIT" in content, "WorkingDirectory should have placeholder comment"


def test_service_section_restart_settings():
    """Service section must contain Restart=on-failure and RestartSec=5."""
    with open(SERVICE_FILE) as f:
        content = f.read()
    
    service_match = re.search(r"\[Service\](.*?)(?:\[|$)", content, re.DOTALL)
    service_content = service_match.group(1)
    
    assert "Restart=on-failure" in service_content, "Missing Restart=on-failure"
    assert "RestartSec=5" in service_content, "Missing RestartSec=5"


def test_install_section_wanted_by():
    """Install section must contain WantedBy=multi-user.target."""
    with open(SERVICE_FILE) as f:
        content = f.read()
    
    install_match = re.search(r"\[Install\](.*?)(?:\[|$)", content, re.DOTALL)
    assert install_match, "Could not find [Install] section"
    install_content = install_match.group(1)
    
    assert "WantedBy=multi-user.target" in install_content, "Missing WantedBy=multi-user.target in [Install]"


def test_inline_comments_documentation():
    """File must contain inline comments documenting install steps."""
    with open(SERVICE_FILE) as f:
        content = f.read()
    
    # Check for install-related comments
    assert "systemctl" in content.lower(), "Missing systemctl documentation in comments"
    assert "daemon-reload" in content.lower() or "enable" in content.lower() or "start" in content.lower(), "Missing install step comments"
