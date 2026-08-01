Bạn đang làm việc trực tiếp trong local sau khi Phase 7 đã hoàn thành. Trước khi thay đổi code, hãy đọc kỹ repository hiện tại, đặc biệt:

* `docs/architecture/Phase-7.md`
* `docs/architecture/Phase-6-plus.md`
* `docs/architecture/appendix-user-interface.md`
* `apps/desktop_agent/`
* `apps/web_portal/`
* `services/gateway/`
* `native/autocad_managed_host/`
* các packaging/install scripts hiện có
* README và các runbook hiện tại

Không được giả định kiến trúc dựa trên nội dung prompt này nếu code hiện tại đã khác. Code trên `main` là source of truth.

# Nhiệm vụ

Biến hệ thống hiện tại sau Phase 7 thành một **Alpha Product có thể đưa cho người dùng thật cài đặt và sử dụng**, thay vì một engineering prototype cần clone repo, chạy PowerShell thủ công, chỉnh environment variable hoặc hiểu kiến trúc nội bộ.

Đây là một vòng **productization**, không phải Phase 8 và không phải một đợt mở rộng CAD capability.

Mục tiêu cuối cùng:

> Một người dùng không phải developer, có Windows và AutoCAD Mechanical 2025, có thể nhận một bộ cài, cài sản phẩm, liên kết máy với tài khoản, mở AutoCAD, kết nối ChatGPT và sử dụng ChatGPT để đọc bản vẽ, preview thay đổi, xác nhận thay đổi an toàn và rollback những commit Phase 7 đủ điều kiện.

Hãy IMPLEMENT sản phẩm này, không chỉ viết proposal hoặc roadmap.

---

# 1. Product scope bắt buộc

Alpha chỉ chính thức support:

* Windows 10/11 x64
* AutoCAD Mechanical 2025 / R25
* Managed .NET runtime
* một user / một Windows desktop tại một thời điểm
* ChatGPT → public Gateway → Desktop Agent → Managed Host → AutoCAD
* browser pairing hiện tại
* OAuth/owner isolation hiện tại
* Phase 7 trusted approval
* create-only CAD Program hiện tại
* Phase 7 checkpointed rollback hiện tại

Không mở rộng support claim sang AutoCAD 2018–2024, AutoCAD LT hoặc các runtime chưa có live evidence tương đương.

Không được làm product claim rộng hơn evidence thực tế.

---

# 2. Trải nghiệm người dùng mục tiêu

Tôi muốn user journey cuối cùng gần như sau:

## First install

User download:

`AutoCAD-AI-Alpha-Setup.exe`

hoặc một installer Windows tương đương phù hợp với codebase hiện tại.

User double-click.

Installer phải tự làm tối đa những việc có thể làm an toàn:

1. kiểm tra Windows architecture;
2. detect AutoCAD Mechanical 2025;
3. cài Managed .NET R25 bundle vào vị trí Autodesk đúng chuẩn;
4. cài Desktop Agent;
5. cài các runtime/dependency cần thiết nếu chiến lược packaging hiện tại cho phép;
6. tạo Start Menu shortcut;
7. cấu hình app data/config directory;
8. tạo uninstall entry hoặc ít nhất một uninstall workflow rõ ràng;
9. không yêu cầu user clone GitHub;
10. không yêu cầu Python/uv/repository source trên máy user;
11. không yêu cầu user tự chạy PowerShell mỗi lần sử dụng.

Nếu AutoCAD đang chạy và cần đóng để install, UI phải nói rõ cho user.

Nếu AutoCAD Mechanical 2025 không tồn tại, installer phải fail gracefully và nói rõ phiên bản alpha hỗ trợ.

Không âm thầm cài Managed Host vào unsupported AutoCAD version.

---

# 3. First-run Desktop Agent

Sau cài đặt, Desktop Agent phải trở thành control center của sản phẩm.

UI ưu tiên ngôn ngữ người dùng, không phải thuật ngữ kiến trúc.

Màn hình chính cần thể hiện tối thiểu:

* Account: Connected / Not connected
* Cloud: Connected / Offline
* AutoCAD: detected product/version / Not running
* Current drawing basename
* AI access status
* Pending approval
* Last operation/result
* Pause AI
* Diagnostics
* Connect account / Reconnect
* Unpair device khi phù hợp

Không đưa các thuật ngữ sau lên main UI trừ diagnostics:

* WSS
* RuntimeBroker
* `cad.host/1`
* R25
* Named Pipe
* SafeFileIPC
* execution digest
* internal job IDs

Có thể hiển thị technical details trong Diagnostics.

---

# 4. Account/device onboarding

Tận dụng browser pairing và Portal hiện tại.

Desired flow:

Desktop Agent
→ “Connect account”
→ mở browser
→ login OAuth
→ xác nhận đúng device
→ browser báo thành công
→ Agent tự nhận trạng thái
→ Cloud Connected.

Không yêu cầu user copy token, API key, JWT, device secret hoặc chỉnh config bằng tay.

Preserve toàn bộ security properties hiện có:

* owner scoping;
* device identity;
* DPAPI/private key protection;
* replay protection;
* replacement/revoke behavior;
* Portal HttpOnly/BFF boundary;
* CSRF/origin protection.

Không được đơn giản hóa onboarding bằng cách phá những boundary này.

---

# 5. ChatGPT onboarding

Alpha phải có một user-facing flow rõ ràng để kết nối ChatGPT với public MCP Gateway hiện tại.

Mục tiêu là user không cần hiểu:

* OAuth issuer;
* audience;
* Cloudflare;
* FastMCP;
* Streamable HTTP;
* WSS;
* Gateway internals.

Tạo một trang hoặc phần hướng dẫn kiểu:

“Connect ChatGPT”

với các bước tối thiểu cần thiết cho alpha environment hiện tại.

Nếu ChatGPT vẫn yêu cầu user thêm MCP connector trong Developer Mode, hãy hướng dẫn chính xác phần đó, nhưng toàn bộ server/tunnel/Gateway infrastructure phải được operator vận hành phía server, không được bắt mỗi tester chạy Cloudflare tunnel trên máy của họ.

Alpha Desktop Agent phải outbound-connect tới Gateway. Không mở public listener trên máy user.

---

# 6. Alpha CAD capabilities

Không cố expose tất cả AutoCAD MCP capability.

Alpha product workflow cần tập trung vào:

## Read

User có thể hỏi ChatGPT:

* “Bản vẽ hiện tại có gì?”
* “Có bao nhiêu đối tượng?”
* “Liệt kê layer.”
* “Liệt kê các loại entity.”
* “Cho tôi biết thông tin bản vẽ hiện tại.”

Sử dụng read path hiện tại, không xây read architecture mới.

## Create-only write

Giữ đúng CAD Program registry hiện tại.

Không mở broad modify/delete.

User phải có thể yêu cầu các create-only operation đã được Phase 6/7 hỗ trợ.

Ví dụ:

“Create layer AI-DEMO and draw a circle radius 500 at 1000,1000.”

Expected high-level flow:

ChatGPT
→ prepare
→ preview
→ commit admission
→ Phase 7 approval
→ one durable commit job
→ exact receipt
→ validate.

Không bypass Phase 7 approval để làm UX đơn giản hơn.

---

# 7. Preview và trusted approval

Đây là feature sản phẩm trung tâm.

Khi một write cần approval, Desktop Agent phải cho user hiểu:

* ChatGPT muốn làm gì;
* drawing nào bị tác động;
* số lượng/type đối tượng dự kiến được tạo;
* đây là CREATE operation;
* Approve;
* Deny.

Không hiển thị raw model text như trusted data nếu architecture Phase 7 đã cấm điều đó.

Approval phải bind đúng immutable execution intent hiện tại.

Model/ChatGPT tuyệt đối không được:

* approve chính nó;
* giảm assurance requirement;
* tự tạo approval proof;
* thay đổi intent sau approval.

Không tạo direct-commit bypass mới.

---

# 8. Rollback UX

Productize Phase 7 rollback hiện tại.

Sau một successful eligible Phase 7 commit, user cần có khả năng thấy:

“Undo AI change”

hoặc wording tương đương.

Đây KHÔNG phải AutoCAD generic Undo.

Flow phải sử dụng:

checkpoint
→ rollback preview
→ conflict detection
→ trusted approval nếu policy yêu cầu
→ rollback commit
→ rollback receipt
→ validate.

Nếu drawing đã thay đổi và rollback không còn conflict-free:

* fail closed;
* giải thích cho user rằng thay đổi không thể rollback tự động an toàn;
* không cố erase handle trực tiếp;
* không dùng generic Undo;
* không xóa shared layer/object.

Old Phase 6 receipt không được trình bày là rollbackable.

---

# 9. Recovery UX

Phase 7 có RecoveryCase/outcome_unknown semantics.

Đưa chúng thành UX mà user/operator hiểu được.

Không hiển thị một write đang `outcome_unknown` như “Failed — Retry”.

Thay vào đó, UI cần nói đại ý:

“AutoCAD may have completed this operation, but confirmation was lost. The system is checking the drawing before allowing another write.”

Provide:

* status;
* retry reconciliation/read-only recovery action nếu architecture cho phép;
* diagnostics;
* support-safe identifier.

Không cung cấp nút re-execute write.

Không giải phóng retained lock tùy tiện.

Không cho user “Mark successful” bằng tay.

---

# 10. Hard pause và kill safety

`Pause AI` phải là chức năng thật, không chỉ cosmetic.

Khi pause:

* không release write mới;
* xử lý pending intent/approval/job đúng semantics Phase 7;
* không làm trạng thái đang chạy trở nên giả;
* read-only behavior có thể tiếp tục hay không tùy policy hiện tại, nhưng UX phải rõ.

Preserve feature gates và fail-closed defaults.

---

# 11. Packaging

Hãy rà toàn bộ packaging hiện tại và chọn giải pháp alpha nhỏ nhất, đáng tin cậy nhất.

Không xây full enterprise updater ecosystem.

Alpha chỉ cần:

* install;
* upgrade từ alpha build trước nếu hợp lý;
* uninstall;
* rollback installer/package khi install failure;
* version information;
* package integrity verification;
* deterministic artifact output.

Ưu tiên tái sử dụng signed R25 bundle/install/rollback engineering hiện có.

Không xóa các security/hash/signature checks hiện tại để làm installer dễ hơn.

Nếu production CA certificate/timestamp chưa có, alpha phải được ghi rõ là controlled alpha và installer UX phải phản ánh đúng trust state. Không giả vờ production-signed.

---

# 12. Configuration

Người dùng bình thường không được phải chỉnh environment variables.

Tạo một alpha configuration mechanism thích hợp:

* packaged defaults;
* machine/user local config;
* environment overrides chỉ dành cho development/operator.

Public URLs, portal URL và Gateway settings của alpha environment phải được inject trong build/release config thay vì bắt tester nhập.

Secrets không được hard-code vào repository hoặc installer.

---

# 13. Startup behavior

Sau cài đặt:

* Desktop Agent có thể start cùng Windows hoặc user có thể bật tùy chọn này;
* Agent dùng system tray;
* nếu AutoCAD chưa chạy, trạng thái đơn giản là “Waiting for AutoCAD”;
* khi AutoCAD mở, Agent detect lại;
* khi drawing đổi, UI update;
* Gateway reconnect tự động theo semantics hiện tại;
* không spam popup.

Approval là một trong số ít trường hợp được phép thu hút attention rõ ràng.

---

# 14. Diagnostics dành cho alpha

Thêm một chức năng “Copy diagnostics” hoặc “Export diagnostics”.

Nó phải hữu ích cho support nhưng không leak:

* OAuth access token;
* refresh token;
* private key;
* device polling secret;
* raw drawing contents;
* raw CAD Program;
* full private file paths khi không cần thiết;
* arbitrary model text.

Nên chứa các field an toàn như:

* app version;
* Agent version;
* Managed Host package version/hash;
* detected AutoCAD product/version;
* connection state;
* safe error code;
* last operation category;
* timestamps;
* relevant recovery state;
* installer receipt/version.

---

# 15. Feedback

Alpha cần feedback loop cực nhỏ.

Sau operation thành công/thất bại, có thể cung cấp:

“Worked”
“Didn't work”

và reason categories đơn giản.

Nếu implementation này làm scope tăng đáng kể, ưu tiên installer/onboarding/E2E trước feedback.

Không gửi bản vẽ hoặc prompt riêng tư làm telemetry mặc định.

---

# 16. README và documentation

README hiện tại phải được rà lại từ đầu.

README public không được tiếp tục làm một user mới nghĩ rằng cách sử dụng sản phẩm alpha là:

git clone
→ uv sync
→ chỉnh Support Paths
→ chạy tunnel
→ chỉnh env vars.

Tách rõ:

## For users

`docs/ALPHA-QUICKSTART.md`

Mục tiêu: đủ để tester cài và chạy trong vài phút.

Nội dung gần như:

1. Requirements
2. Download installer
3. Install
4. Open AutoCAD Mechanical 2025
5. Connect account
6. Connect ChatGPT
7. Try first prompt
8. Approve change
9. Rollback AI change
10. Troubleshooting
11. Uninstall
12. Report issue

## For developers

Giữ các hướng dẫn source/dev riêng.

Sửa mọi stale repo URL, stale product claim và tài liệu Phase cũ nếu nó gây hiểu nhầm cho alpha onboarding.

Không rewrite toàn bộ historical architecture docs chỉ để làm đẹp.

---

# 17. Demo prompts

Alpha Quick Start phải có sẵn khoảng 5 prompt deterministic-ish:

1.

“Describe the AutoCAD drawing I currently have open.”

2.

“List the layers in my current drawing.”

3.

“Count the main entity types in this drawing.”

4.

“Create a layer named AI-DEMO and draw a circle with radius 500 at coordinate 1000,1000. Preview the change before applying it.”

5.

“Create a simple 4000 x 3000 rectangular polyline on layer AI-DEMO and add text AI DEMO inside it. Preview before applying.”

Chỉ chọn prompt phù hợp chính xác với CAD Program operations thực tế đang được support trên `main`. Nếu registry hiện tại khác, điều chỉnh demo prompt cho đúng code.

---

# 18. Không được làm

Trong vòng alpha này KHÔNG:

* implement Phase 8;
* xây Scene Graph;
* xây shopdrawing engine;
* thêm AI planning architecture mới;
* thêm arbitrary LISP;
* thêm arbitrary AutoCAD commands;
* thêm arbitrary C#/DLL execution;
* broad modify/delete;
* AutoCAD LT write;
* support mọi AutoCAD release;
* PostgreSQL/multi-worker migration;
* organization/team/billing;
* full auto-update platform;
* plugin marketplace;
* collaboration;
* semantic merge;
* cloud CAD execution.

Chỉ làm những gì cần thiết để người thật **install → connect → use → approve → recover/rollback**.

---

# 19. Security invariants không được phá

Phase 7 safety semantics là product requirement, không phải implementation detail.

Phải giữ:

* immutable execution intent;
* trusted approval outside model;
* one-time consent;
* one intent release → at most one durable effect-bearing job;
* exact receipt authority;
* no success inference from revision/entity count/WSS reconnect;
* no blind retry after started/unknown write;
* owner isolation;
* append-only evidence;
* durable RecoveryCase;
* checkpoint-owned rollback only;
* conflict-safe rollback;
* fail-closed stale environment behavior;
* hard pause;
* revoke/session replacement behavior;
* no arbitrary code;
* no silent runtime fallback.

Nếu UX requirement xung đột với một invariant này, invariant thắng.

---

# 20. Testing

Không chỉ test unit.

Sau implementation, chạy toàn bộ test liên quan và regression suites.

Ít nhất phải cover:

* contracts;
* Gateway;
* Desktop Agent;
* Managed Host Core;
* R25 build;
* Portal;
* Portal E2E;
* packaging/install tests;
* Phase 0–7 regression liên quan;
* installer clean install;
* upgrade;
* uninstall;
* failed install rollback;
* first-run pairing;
* reconnect;
* approval;
* deny;
* expired approval;
* pause;
* revoke/re-pair;
* commit;
* duplicate request;
* outcome_unknown/recovery;
* successful rollback;
* rollback conflict.

Không sửa test bằng cách nới safety assertions chỉ để suite xanh.

---

# 21. Live acceptance

Nếu môi trường có AutoCAD Mechanical 2025 thật, thực hiện live acceptance.

Happy path tối thiểu:

fresh alpha install
→ Agent launch
→ browser pairing
→ AutoCAD Mechanical 2025 detection
→ open test DWG
→ ChatGPT read
→ prepare create-only program
→ preview
→ approval appears
→ approve
→ exactly one commit
→ exact receipt
→ validate
→ restart Agent
→ reconnect
→ inspect previous result
→ rollback preview
→ approve rollback
→ rollback
→ validate entities removed.

Ngoài happy path, chạy các Phase 7 safety drills có thể thực hiện được.

Đặc biệt verify:

* repeated ChatGPT commit request không tạo effect thứ hai;
* disconnect/reconnect không biến unknown thành retry;
* denied approval không dispatch write;
* changed checkpoint entity khiến rollback fail closed;
* unrelated strict revision conflict được xử lý đúng;
* replaced/revoked session không approve được.

Nếu môi trường KHÔNG có AutoCAD Mechanical 2025 thật:

* không fabricate live evidence;
* hoàn thành code/test/packaging có thể làm được;
* tạo rõ một `ALPHA-LIVE-ACCEPTANCE.md` với những bước còn phải chạy trên máy thật;
* trạng thái cuối phải nói rõ automated pass nhưng live customer acceptance còn pending.

---

# 22. Definition of Done

Không coi task hoàn thành chỉ vì code compile hoặc test xanh.

Alpha hoàn thành khi một tester mới có thể làm workflow sau mà không clone repo hoặc mở terminal:

1. tải installer;
2. cài;
3. launch Agent;
4. login/pair;
5. mở AutoCAD Mechanical 2025;
6. thấy Agent báo ready;
7. connect ChatGPT theo Quick Start;
8. ChatGPT đọc drawing;
9. ChatGPT chuẩn bị một create-only change;
10. tester preview được;
11. tester approve ngoài model;
12. AutoCAD thay đổi đúng một lần;
13. tester xem kết quả;
14. tester rollback một eligible AI change;
15. tester export diagnostics nếu có lỗi;
16. tester uninstall được sản phẩm.

Ngoài ra:

* clean install phải reproducible;
* installer artifact phải nằm trong một release/dist location rõ ràng;
* artifact phải có version;
* no secrets trong artifact/repo;
* user docs phải khớp build thực tế;
* feature/support claims phải khớp evidence;
* regression suites phải xanh;
* known limitations phải được ghi rõ.

---

# 23. Cách làm việc

Bắt đầu bằng cách inspect repo thật.

Sau đó:

1. lập một inventory rất ngắn về những thành phần đã tồn tại và những gap thật sự để đạt Alpha DoD;
2. triển khai theo vertical slices, ưu tiên đường đi của user hơn refactor;
3. reuse architecture hiện tại;
4. tránh abstraction mới nếu không cần thiết;
5. giữ commit/change set dễ review;
6. chạy test sau từng slice;
7. sửa bug phát hiện được;
8. tiếp tục cho đến khi Alpha DoD đạt tối đa có thể trong môi trường hiện tại.

Đừng dừng sau khi viết một kế hoạch.

Đừng hỏi tôi xác nhận từng bước.

Đừng mở một Phase kiến trúc mới.

Đừng over-engineer.

Khi gặp lựa chọn giữa:
“kiến trúc đẹp hơn”
và
“tester cài được sản phẩm an toàn hơn”

hãy ưu tiên phương án thứ hai, miễn là không phá security invariants.

---

# 24. Deliverables cuối cùng

Khi hoàn thành, báo cáo cho tôi:

## A. Alpha artifact

* installer/build artifact là gì;
* nằm ở đâu;
* cách build release lại;
* version;
* hash/signing status.

## B. User journey

Mô tả chính xác:

Install
→ Pair
→ AutoCAD ready
→ ChatGPT
→ Read
→ Preview
→ Approve
→ Commit
→ Rollback.

## C. Files changed

Nhóm theo:

* installer/packaging;
* Desktop Agent;
* Portal;
* Gateway;
* Managed Host;
* configuration;
* documentation;
* tests.

## D. Validation

Liệt kê command đã chạy và kết quả thật.

Không nói “should pass”.

## E. Live evidence

Nêu rõ phần nào thực sự chạy trên AutoCAD Mechanical 2025 thật và phần nào chưa chạy.

## F. Remaining Alpha blockers

Chỉ liệt kê blocker thực sự ngăn đưa cho tester.

Phân biệt rõ:

* Alpha blocker
* Production blocker
* Future feature

Không coi CA production signing, full AutoCAD version matrix hoặc Phase 8 feature là Alpha blocker trừ khi chúng thực sự ngăn controlled alpha sử dụng.

Mục tiêu của vòng này rất đơn giản:

**Hãy biến repository sau Phase 7 từ một hệ thống engineering đã chứng minh nhiều primitive thành một sản phẩm alpha mà một người dùng AutoCAD thật có thể cài, kết nối ChatGPT và sử dụng mà không cần hiểu repository.**
