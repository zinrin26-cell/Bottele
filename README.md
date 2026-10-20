# Telegram Link-Earn Bot (starter)

## Có gì
- Nút menu: thông tin tài khoản, số dư, số link đã hoàn thành, ngân hàng/STK/tên chủ tài khoản.
- Người dùng cập nhật thông tin ngân hàng bằng 3 dòng.
- Admin thêm nhiệm vụ bằng `/addlink Tên | https://example.com | 1000 | 100`.
- Mỗi người chỉ nhận một claim cho mỗi nhiệm vụ; key số 10 chữ số, duy nhất trong DB.
- `/key MÃ_KEY` chỉ cộng thưởng nếu key thuộc chính người dùng, còn pending, nhiệm vụ còn lượt.
- Rút 5.000–1.000.000đ; tiền được giữ lại khi tạo yêu cầu để tránh rút trùng.
- Yêu cầu gửi tới kênh admin; admin bấm **Duyệt**, chuyển khoản thật xong bấm **Đã chuyển**; khi đó bot đăng thông báo vào nhóm rút tiền.
- Trang web `/claim/<key>` phát/hiển thị key.

## Chạy local
1. Python 3.11+.
2. `pip install -r requirements.txt`
3. Sao chép `.env.example` thành `.env`, điền giá trị. Cách đơn giản: đặt biến môi trường trong terminal trước khi chạy.
4. `python bot.py`

Ví dụ biến môi trường trong shell:
`export BOT_TOKEN='token-cua-ban'`
`export ADMIN_IDS='123456789'`
`export ADMIN_CHANNEL_ID='-1001234567890'`
`export WITHDRAW_GROUP_ID='-1009876543210'`
`export WEB_BASE_URL='http://127.0.0.1:9898'`
`export WEB_ADMIN_SECRET='thay-bang-chuoi-ngau-nhien-dai'`
`python bot.py`

## Lệnh admin
- `/addlink Tên nhiệm vụ | https://domain.example/task | 1000 | 100`
- `/links`
- `/withdrawals`
- `/stats`
- `/credit user_id số_tiền`

`ADMIN_IDS` là Telegram numeric user ID, không phải username. Bot phải được thêm làm admin vào kênh admin và có quyền gửi tin; thêm bot vào nhóm rút tiền và cho quyền gửi tin.

## Deploy Render
- Đẩy source lên GitHub, tạo Render Web Service từ repo.
- Build: `pip install -r requirements.txt`
- Start: `python bot.py`
- Thêm các Environment Variables giống `.env.example`.
- `WEB_BASE_URL` phải là URL HTTPS của Render service.
- SQLite trên filesystem của dịch vụ có thể mất dữ liệu khi redeploy/restart hoặc khi dùng filesystem không persistent. Dùng Render Persistent Disk hoặc PostgreSQL trước khi chạy thật có tiền.
- Một process chạy polling bot và Flask web cùng lúc. Không chạy nhiều instance polling với cùng token.

## Quan trọng về xác minh link
Bản starter này phát key sau khi người dùng bấm nút trên trang web. Nó **không thể tự xác minh** rằng người dùng thật sự hoàn thành link bên thứ ba. Muốn chống gian lận cần tích hợp callback/postback/API từ nhà cung cấp link rút gọn; không có API đó thì không nên coi việc bấm nút là bằng chứng hoàn thành.
