# Hướng dẫn Khầy chạy kiểm thử clean VM cho Phase 5

Ngày: 2026-07-25

## Mục đích

Hướng dẫn này giúp Khầy chạy thay phần kiểm thử mà phiên Codex hiện tại không
có quyền thực hiện trên Hyper-V.

Bài kiểm thử sẽ:

1. chép hai bản phát hành R25 đã ký thử nghiệm vào máy ảo;
2. cài mới bản v1 trong một thư mục kiểm thử riêng;
3. nâng cấp lên bản v2;
4. rollback từ v2 về đúng bản v1;
5. rollback lần nữa để kiểm tra cài mới được gỡ sạch;
6. chép kết quả về:
   `D:\AI\autocad-mcp\dist\phase5-clean-vm-evidence.json`.

Bài kiểm thử **không cài vào thư mục AutoCAD thật**, không sửa bản vẽ và không
tự khởi động, tắt hoặc khôi phục checkpoint của máy ảo.

## Trước khi chạy

Khầy cần có:

- máy ảo Hyper-V sạch tên `Phase4-Win11-Clean`;
- tài khoản và mật khẩu đăng nhập Windows bên trong máy ảo;
- quyền quản trị Hyper-V trên máy thật;
- hai thư mục sau vẫn còn:
  - `D:\AI\autocad-mcp\dist\phase5-signed-r25-v1`
  - `D:\AI\autocad-mcp\dist\phase5-signed-r25-v2`

Nếu tên máy ảo khác, thay `Phase4-Win11-Clean` trong các lệnh bên dưới bằng tên
thật. Nếu một trong hai thư mục release bị thiếu, dừng lại và báo tao; không tự
tạo lại certificate hoặc release.

## Bước 1 — Mở PowerShell bằng quyền quản trị

1. Mở menu **Start**.
2. Gõ `PowerShell 7` hoặc `pwsh`.
3. Nhấp chuột phải và chọn **Run as administrator**.
4. Chọn **Yes** khi Windows hỏi.

Trong cửa sổ vừa mở, chạy:

```powershell
Set-Location D:\AI\autocad-mcp
```

## Bước 2 — Kiểm tra đủ file

Sao chép nguyên khối lệnh này vào PowerShell:

```powershell
Test-Path .\dist\phase5-signed-r25-v1
Test-Path .\dist\phase5-signed-r25-v2
Test-Path .\scripts\test-phase5-clean-vm-rollback.ps1
```

Kết quả đúng là ba dòng đều hiện:

```text
True
True
True
```

Nếu có dòng `False`, dừng lại và báo tao.

## Bước 3 — Kiểm tra và mở máy ảo

Chạy:

```powershell
Get-VM -Name Phase4-Win11-Clean
```

Nếu cột `State` là `Off`, mở máy ảo bằng:

```powershell
Start-VM -Name Phase4-Win11-Clean
```

Sau đó mở **Hyper-V Manager**, kết nối vào máy ảo và chờ đến màn hình đăng
nhập Windows. Không cần mở AutoCAD trong máy ảo.

Chạy lại lệnh sau và chắc chắn `State` là `Running`:

```powershell
Get-VM -Name Phase4-Win11-Clean
```

Nếu PowerShell báo `You do not have the required permission`, kiểm tra lại cửa
sổ đang chạy bằng **Run as administrator**. Nếu vẫn lỗi, dừng lại và gửi nguyên
văn lỗi cho tao.

## Bước 4 — Nhập tài khoản của máy ảo

Chạy:

```powershell
$credential = Get-Credential
```

Một hộp thoại sẽ hiện ra. Nhập tài khoản và mật khẩu Windows **của máy ảo**,
không phải mật khẩu GitHub.

PowerShell Direct cần **mật khẩu thật**, không dùng được mã PIN/Windows Hello.
Nên dùng một tài khoản có quyền Administrator bên trong máy ảo vì bài kiểm thử
cần tạo thư mục riêng dưới ổ `C:`.

Nếu chưa chắc tên tài khoản:

1. kết nối vào máy ảo bằng Hyper-V Manager;
2. đăng nhập Windows trong máy ảo;
3. mở PowerShell bên trong máy ảo;
4. chạy `whoami`;
5. dùng nguyên kết quả đó làm tên tài khoản trong `Get-Credential`.

Tài khoản cục bộ thường có dạng bên dưới. Phần đầu là tên máy Windows hiển thị
**bên trong máy ảo**; tên này có thể khác tên `Phase4-Win11-Clean` trong
Hyper-V:

```text
<tên-máy-Windows-trong-VM>\<tên-tài-khoản>
```

Sau khi nhập lại, nên kiểm tra riêng tài khoản trước khi chạy bài kiểm thử:

```powershell
$testSession = New-PSSession `
  -VMName Phase4-Win11-Clean `
  -Credential $credential

Invoke-Command -Session $testSession {
  whoami
  hostname
}

Remove-PSSession $testSession
```

Nếu hai dòng tên tài khoản và tên máy hiện ra mà không có lỗi màu đỏ, tài khoản
đã đúng.

## Bước 5 — Chạy bài kiểm thử

Sao chép nguyên khối lệnh này:

```powershell
.\scripts\test-phase5-clean-vm-rollback.ps1 `
  -VMName Phase4-Win11-Clean `
  -Credential $credential `
  -ReleaseV1Root .\dist\phase5-signed-r25-v1 `
  -ReleaseV2Root .\dist\phase5-signed-r25-v2 `
  -EvidencePath .\dist\phase5-clean-vm-evidence.json
```

Chờ PowerShell chạy xong và hiện lại dấu nhắc lệnh. Thường chỉ mất vài phút.

Harness đã tự dùng `ExecutionPolicy Bypass` chỉ cho tiến trình kiểm thử bên
trong VM. Nó không thay đổi chính sách lâu dài của máy ảo. Vì vậy nếu thấy lỗi
execution policy từ bản script cũ, hãy cập nhật workspace rồi chạy lại Bước 5.

## Bước 6 — Kiểm tra kết quả

Chạy:

```powershell
$evidence = Get-Content `
  .\dist\phase5-clean-vm-evidence.json `
  -Raw | ConvertFrom-Json

$evidence | Format-List

$allPassed = (
  $evidence.work_root_was_absent -eq $true -and
  $evidence.clean_install -eq "passed" -and
  $evidence.upgrade -eq "passed" -and
  $evidence.upgrade_rollback -eq "passed" -and
  $evidence.clean_install_rollback -eq "passed" -and
  $evidence.v1_bundle_hash -eq $evidence.restored_v1_bundle_hash
)

if ($allPassed) {
  "OK - CLEAN VM PASSED"
} else {
  "KHONG DAT - GUI KET QUA CHO CODEX"
}
```

Kết quả đạt phải có dòng:

```text
OK - CLEAN VM PASSED
```

Ngoài ra, bốn mục sau phải là `passed`:

- `clean_install`
- `upgrade`
- `upgrade_rollback`
- `clean_install_rollback`

Hai giá trị `v1_bundle_hash` và `restored_v1_bundle_hash` phải giống hệt nhau.

## Bước 7 — Báo lại cho tao

Nếu đạt, Khầy chỉ cần nhắn:

```text
OK clean VM, cập nhật tài liệu
```

Tao sẽ tự đọc file
`D:\AI\autocad-mcp\dist\phase5-clean-vm-evidence.json`, kiểm tra lại hash và
chỉ khi bằng chứng hợp lệ mới cập nhật tài liệu/PR.

Nếu không đạt, đừng tự sửa hoặc chạy đi chạy lại nhiều lần. Gửi cho tao:

1. toàn bộ dòng lỗi màu đỏ trong PowerShell;
2. kết quả của `Get-VM -Name Phase4-Win11-Clean`;
3. cho biết lỗi xảy ra ở Bước 2, 3, 4, 5 hay 6.

## Những việc không cần làm

- Không cần mở AutoCAD.
- Không cần cài bundle vào `Autodesk\ApplicationPlugins`.
- Không cần tạo certificate mới.
- Không cần tắt antivirus hoặc Windows Defender.
- Không cần gửi mật khẩu máy ảo cho tao.
- Chưa commit file evidence và chưa đánh dấu production-certified.
