from pathlib import Path

path = Path(__file__).with_name("phase1_1_bootstrap_part2.py")
text = path.read_text(encoding="utf-8")
needle = "replace_once('services/gateway/src/autocad_gateway/services.py', '        status = await self.backend.status()\\n', '        status = await self.application_service.get_status()\\n')\n"
if text.count(needle) != 2:
    raise RuntimeError(f"expected two gateway status replacements, found {text.count(needle)}")
replacement = '''gateway_services = 'services/gateway/src/autocad_gateway/services.py'
gateway_content = read(gateway_services)
gateway_old_status = '        status = await self.backend.status()\\n'
gateway_new_status = '        status = await self.application_service.get_status()\\n'
if gateway_content.count(gateway_old_status) != 2:
    raise RuntimeError(
        f"{gateway_services}: expected two status reads, "
        f"found {gateway_content.count(gateway_old_status)}"
    )
write(gateway_services, gateway_content.replace(gateway_old_status, gateway_new_status))
'''
text = text.replace(needle, replacement, 1)
text = text.replace(needle, "", 1)
path.write_text(text, encoding="utf-8", newline="\n")
