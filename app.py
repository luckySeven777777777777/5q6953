"""
缅甸客户手机号收集系统
扫码 → 输入手机号 → 后台存储 → 管理端查看
"""
import os
import json
import qrcode
import requests
import threading
from io import BytesIO
from datetime import datetime
from flask import Flask, request, jsonify, render_template, send_file

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "data", "phone_numbers.json")
QR_FILE = os.path.join(BASE_DIR, "static", "qrcode.png")

os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
os.makedirs(os.path.dirname(QR_FILE), exist_ok=True)


def load_data():
    if not os.path.exists(DATA_FILE):
        return []
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_public_url():
    """从环境变量或请求推断公网地址，优先使用显式配置"""
    env_url = os.environ.get("PUBLIC_URL", "").strip().rstrip("/")
    if env_url:
        return env_url
    return None


def send_telegram(name, phone, feedback, submit_time):
    """异步发送 Telegram 通知"""
    bot_token = os.environ.get("TG_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TG_CHAT_ID", "").strip()
    if not bot_token or not chat_id:
        return

    msg = (
        f"\U0001F4E5 <b>New Registration</b>\n\n"
        f"\U0001F464 <b>Name:</b> {name}\n"
        f"\U0001F4DE <b>Phone:</b> +95 {phone}\n"
        f"\U0001F4AC <b>Feedback:</b> {feedback or 'None'}\n"
        f"\U0001F550 <b>Time:</b> {submit_time}"
    )

    def _send():
        try:
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            requests.post(url, json={
                "chat_id": chat_id,
                "text": msg,
                "parse_mode": "HTML"
            }, timeout=10)
        except Exception:
            pass

    threading.Thread(target=_send, daemon=True).start()


# ==================== 客户扫码页面 ====================
@app.route("/")
def index():
    """客户扫码后看到的手机号提交页面"""
    return render_template("index.html")


# ==================== 提交手机号 ====================
@app.route("/submit", methods=["POST"])
def submit():
    """接收客户提交的名字、手机号、反馈"""
    name = request.form.get("name", "").strip()
    phone = request.form.get("phone", "").strip()
    feedback = request.form.get("feedback", "").strip()

    if not name:
        return jsonify({"ok": False, "message": "Please enter your name / အမည်ထည့်ပါ"}), 400
    if not phone:
        return jsonify({"ok": False, "message": "Please enter your phone number / ဖုန်းနံပါတ်ထည့်ပါ"}), 400

    # 基本格式校验：允许 +95 开头或 09 开头的缅甸号码
    raw = phone.replace(" ", "").replace("-", "")
    if raw.startswith("+95"):
        digits = raw[3:]
    elif raw.startswith("09"):
        digits = raw
    elif raw.startswith("959"):
        digits = "0" + raw[2:]
    else:
        digits = raw

    # 剔除非数字
    digits = "".join(c for c in digits if c.isdigit())
    if len(digits) < 8 or len(digits) > 12:
        return jsonify({"ok": False, "message": "Invalid phone number / ဖုန်းနံပါတ်မမှန်ပါ"}), 400

    data = load_data()
    # 检查是否已存在（按手机号去重）
    for entry in data:
        if entry["phone"] == digits:
            submit_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            send_telegram(name, digits, feedback, submit_time)
            return jsonify({
                "ok": True,
                "message": "Thank you! Your number is already registered. / ကျေးဇူးတင်ပါတယ်၊ သင့်နံပါတ်မှတ်ပုံတင်ပြီးပါပြီ။",
                "duplicate": True
            })

    submit_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = {
        "name": name,
        "phone": digits,
        "raw_input": phone,
        "feedback": feedback,
        "time": submit_time,
    }
    data.append(entry)
    save_data(data)

    # 发送 Telegram 通知
    send_telegram(name, digits, feedback, submit_time)

    return jsonify({
        "ok": True,
        "message": "Thank you! Your info has been received. / ကျေးဇူးတင်ပါတယ်၊ သင့်အချက်အလက်လက်ခံရရှိပါပြီ။"
    })


# ==================== 管理后台 ====================
@app.route("/admin")
def admin():
    """管理端：查看收集到的手机号"""
    return render_template("admin.html")


@app.route("/api/phones")
def api_phones():
    """返回手机号列表（JSON）"""
    data = load_data()
    return jsonify({"total": len(data), "list": data})


@app.route("/api/phones/clear", methods=["POST"])
def clear_phones():
    """清空所有手机号"""
    save_data([])
    return jsonify({"ok": True, "message": "All records cleared"})


@app.route("/api/phones/export")
def export_csv():
    """导出 CSV"""
    data = load_data()
    import csv
    from io import StringIO
    si = StringIO()
    writer = csv.writer(si)
    writer.writerow(["Name", "Phone", "Raw Input", "Feedback", "Time"])
    for entry in data:
        writer.writerow([entry.get("name", ""), entry["phone"], entry.get("raw_input", ""), entry.get("feedback", ""), entry["time"]])
    output = si.getvalue()
    from flask import Response
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=phone_numbers.csv"}
    )


# ==================== 二维码动态生成 ====================
@app.route("/qrcode.png")
def qrcode_image():
    """动态生成二维码（指向当前公网地址或本机地址）"""
    public_url = get_public_url()
    if public_url:
        target_url = public_url
    else:
        # 回退：使用请求中的 host
        host = request.host
        scheme = request.scheme
        target_url = f"{scheme}://{host}/"

    img = qrcode.make(target_url)
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")


def generate_qrcode_file(target_url):
    """生成静态二维码文件"""
    img = qrcode.make(target_url)
    img.save(QR_FILE)
    print(f"QR code saved → {QR_FILE}")
    print(f"Target URL: {target_url}")
    return QR_FILE


if __name__ == "__main__":
    import sys
    port = int(os.environ.get("PORT", 5000))

    # 检查是否有 --gen-qr 参数来生成静态二维码
    if "--gen-qr" in sys.argv:
        url = os.environ.get("PUBLIC_URL", f"http://localhost:{port}")
        generate_qrcode_file(url)
        print("Done.")
        sys.exit(0)

    print(f"""
╔══════════════════════════════════════════════╗
║    Myanmar Phone Collection System           ║
║    缅甸客户手机号收集系统                       ║
╠══════════════════════════════════════════════╣
║  客户扫码页:  http://localhost:{port}/         ║
║  管理后台:    http://localhost:{port}/admin    ║
║  二维码图片:  http://localhost:{port}/qrcode.png ║
║  导出CSV:     http://localhost:{port}/api/phones/export ║
╚══════════════════════════════════════════════╝
    """)

    app.run(host="0.0.0.0", port=port, debug=True)
