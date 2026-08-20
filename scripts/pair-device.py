"""Interactive device pairing helper for Desktop Agent."""

import asyncio
import json
import os
import shutil
import sys
import time
import webbrowser
from pathlib import Path

import httpx

from autocad_desktop_agent.config import AgentConfig
from autocad_desktop_agent.pairing import DeviceIdentityStore, PairingApiClient

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def setup_env_from_config() -> None:
    local_appdata = os.environ.get("LOCALAPPDATA", str(Path.home()))
    config_file = Path(local_appdata) / "Kythuatvang" / "AutoCADAgent" / "agent-config.json"
    if not config_file.is_file():
        raise FileNotFoundError(f"Chua tim thay file cau hinh: {config_file}")
    
    with open(config_file, "r", encoding="utf-8-sig") as f:
        config_data = json.load(f)
    
    os.environ.setdefault("AUTOCAD_AGENT_IDENTITY_MODE", "browser_pairing")
    os.environ["AUTOCAD_AGENT_GATEWAY_WS_URL"] = str(config_data.get("gateway_ws_url", ""))
    os.environ["AUTOCAD_AGENT_GATEWAY_HTTP_URL"] = str(config_data.get("gateway_http_url", ""))
    os.environ["AUTOCAD_AGENT_PORTAL_URL"] = str(config_data.get("portal_url", ""))
    os.environ["AUTOCAD_AGENT_DEVICE_NAME"] = str(config_data.get("device_name", "May AutoCAD Lab"))
    os.environ["AUTOCAD_AGENT_PACKAGE_PATH"] = str(config_data.get("package_path", ""))
    os.environ["AUTOCAD_AGENT_PACKAGE_SHA256"] = str(config_data.get("package_sha256", ""))
    os.environ["AUTOCAD_MCP_TELEMETRY_ENABLED"] = "0"
    os.environ["AUTOCAD_MCP_PROGRAM_V1_CREATE_PACK_ENABLED"] = "0"
    os.environ["AUTOCAD_MCP_PROGRAM_V1_TRANSFORM_PACK_ENABLED"] = "0"
    os.environ["AUTOCAD_MCP_PROGRAM_V1_SOURCE_ENABLED"] = "0"
    os.environ["AUTOCAD_MCP_CHECKPOINT_V2_ENABLED"] = "0"


async def main() -> None:
    setup_env_from_config()
    config = AgentConfig.from_env()
    identity_dir = config.ledger_path.with_name("identity")
    store = DeviceIdentityStore(identity_dir)
    api = PairingApiClient(
        config.gateway_http_url,
        store,
        portal_url=config.portal_url or config.gateway_http_url,
    )

    # Check if existing device is already registered on Gateway
    if store.has_identity():
        identity = store.load_identity()
        print(f"Thiet bi hien tai: {identity.device_id} (Ten: {config.device_name})")
        try:
            # Test if current device is active on Gateway
            token = await api.session_token()
            print("Trang thai tren Gateway: Da co token hop le.")
        except Exception:
            token = None
        
        print("\nBan co muon tao yeu cau lien ket MOI cho tai khoan Google (hothimytu2761@gmail.com)?")
        print("  1. Tao lien ket moi cho tai khoan (Khuyen nghi)")
        print("  2. Giu nguyen lien ket cu")
        choice = input("Chon [1/2] (mac dinh la 1): ").strip()
        if choice == "2":
            print("\nDa giu nguyen thiet bi hien tai.")
            return

        # Backup old identity and generate fresh device ID
        backup_dir = identity_dir.with_name(f"identity-backup-{int(time.time())}")
        if identity_dir.exists():
            shutil.copytree(identity_dir, backup_dir)
            shutil.rmtree(identity_dir)
            print(f"Da sao luu identity cu vao: {backup_dir.name}")
        
        store = DeviceIdentityStore(identity_dir)
        api = PairingApiClient(
            config.gateway_http_url,
            store,
            portal_url=config.portal_url or config.gateway_http_url,
        )

    print("\nDang gui yeu cau lien ket thiet bi moi toi Gateway...")
    enrollment = await api.start(config.device_name)
    user_code = enrollment.get("user_code", "N/A")
    confirmation_url = str(enrollment.get("confirmation_url", ""))
    pairing_id = str(enrollment["pairing_id"])
    polling_secret = str(enrollment["polling_secret"])
    challenge = str(enrollment["challenge"])
    
    print("\n" + "=" * 64)
    print(f"  MA XAC NHAN LIEN KET (USER CODE):  {user_code}")
    print(f"  DUONG DAN TRINH DUYET:             {confirmation_url}")
    print("=" * 64 + "\n")
    print("Trinh duyet dang mo...")
    print("Hay dang nhap bang tai khoan Google (hothimytu2761@gmail.com) va bam Approve / Dong y.")
    print("Dang cho xac nhan tu trinh duyet (nhan Ctrl+C neu muon huy)...\n")
    
    webbrowser.open(confirmation_url)
    
    while True:
        status = await api.status(pairing_id=pairing_id, polling_secret=polling_secret)
        state = status.get("state") or status.get("status")
        if state == "approved":
            break
        if state in {"denied", "expired", "completed"}:
            raise RuntimeError(f"Yeu cau lien ket ket thuc voi trang thai: {state}")
        await asyncio.sleep(2)
    
    await api.complete(pairing_id=pairing_id, challenge=challenge)
    print("\n" + "*" * 64)
    print("  >>> LIEN KET THIET BI VOI TAI KHOAN THANH CONG! <<<")
    print("*" * 64 + "\n")
    print("Bay gio ban co the quay lai ChatGPT va ra lenh dieu khien AutoCAD.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nDa huy qua trinh lien ket.")
        sys.exit(1)
    except Exception as exc:
        print(f"\nLoi lien ket: {exc}")
        sys.exit(1)
