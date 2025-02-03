import itchat
from itchat.content import TEXT
import requests
import json
import logging
from datetime import datetime
from flask import Flask, render_template
import threading
import time
import os
import webbrowser
from flask_cors import CORS
from models import Session, ChatMessage

# 获取程序根目录
root_dir = os.path.dirname(os.path.abspath(__file__))

# 读取 prompt.md 文件内容
with open(os.path.join(root_dir, 'prompt.md'), 'r', encoding='utf-8') as file:
    prompt_content = file.read()

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# DeepSeek API 配置
DEEPSEEK_API_URL = "https://api.siliconflow.cn/v1/chat/completions"
DEEPSEEK_API_KEY = 'sk-pnqdtbrfrnfcytmwszyeybmavyjzujgrkyqmkaekhijzsxjq'

# 创建Flask应用
app = Flask(__name__, static_folder='static')
CORS(app)

# 全局变量存储上下文
chat_contexts = {}

# 新增全局变量
user_queues = {}  # 用户消息队列管理
queue_lock = threading.Lock()  # 队列访问锁


def save_message(sender_id, sender_name, message, reply):
    """保存聊天记录到数据库"""
    try:
        session = Session()
        chat_message = ChatMessage(
            sender_id=sender_id,
            sender_name=sender_name,
            message=message,
            reply=reply
        )
        session.add(chat_message)
        session.commit()
        session.close()
    except Exception as e:
        logger.error(f"保存消息失败: {str(e)}")


def get_deepseek_response(message, user_id):
    """调用 DeepSeek API 获取回复"""
    try:
        # 获取用户上下文
        if user_id not in chat_contexts:
            chat_contexts[user_id] = []

        # 添加带时间戳的新消息到上下文
        chat_contexts[user_id].append({"role": "user", "content": message})

        # 保持上下文长度不超过10条消息
        if len(chat_contexts[user_id]) > 10:
            chat_contexts[user_id] = chat_contexts[user_id][-10:]

        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }

        data = {
            "model": "deepseek-ai/DeepSeek-V3",
            "messages": [
                {"role": "system", "content": prompt_content},
                *chat_contexts[user_id]
            ]
        }

        response = requests.post(DEEPSEEK_API_URL, headers=headers, json=data, timeout=30)
        response.raise_for_status()
        reply = response.json()['choices'][0]['message']['content']

        # 添加回复到上下文
        chat_contexts[user_id].append({"role": "assistant", "content": reply})

        return reply
    except Exception as e:
        logger.error(f"调用 DeepSeek API 失败: {str(e)}")
        return "抱歉，亚托莉现在休眠中，请稍后再试..."


def process_user_messages(user_id):
    """处理用户消息队列"""
    with queue_lock:
        if user_id not in user_queues:
            return
        user_data = user_queues.pop(user_id)
        messages = user_data['messages']
        sender_name = user_data['sender_name']
        username = user_data['username']

    # 合并消息（保留时间戳）
    merged_message = ' \\ '.join(messages)
    logger.info(f"处理合并消息 ({sender_name}): {merged_message}")

    # 获取API回复
    reply = get_deepseek_response(merged_message, user_id)

    # 发送回复
    try:
        if '\\' in reply:
            parts = [p.strip() for p in reply.split('\\') if p.strip()]
            for part in parts:
                itchat.send(part, toUserName=user_id)
                logger.info(f"分段回复 {sender_name}: {part}")
                time.sleep(1)
        else:
            itchat.send(reply, toUserName=user_id)
            logger.info(f"回复 {sender_name}: {reply}")
    except Exception as e:
        logger.error(f"发送回复失败: {str(e)}")

    # 保存到数据库
    save_message(username, sender_name, merged_message, reply)


@itchat.msg_register([TEXT])
def handle_text(msg):
    """处理文本消息（队列管理）"""
    try:
        username = msg['FromUserName']
        content = msg['Text']

        # 获取发送者信息
        sender = itchat.search_friends(userName=username)
        sender_name = sender['NickName'] if sender else username

        # 添加时间戳
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        time_aware_content = f"[{current_time}] {content}"

        logger.info(f"收到消息 - {sender_name}: {content}")

        with queue_lock:
            # 初始化或更新队列
            if username not in user_queues:
                user_queues[username] = {
                    'timer': threading.Timer(5.0, process_user_messages, args=[username]),
                    'messages': [time_aware_content],
                    'sender_name': sender_name,
                    'username': username
                }
                user_queues[username]['timer'].start()
                logger.info(f"已为 {sender_name} 启动新会话计时器")
            else:
                user_queues[username]['messages'].append(time_aware_content)
                logger.info(
                    f"{sender_name} 的消息已加入队列，当前待处理消息数: {len(user_queues[username]['messages'])}")

    except Exception as e:
        logger.error(f"消息处理失败: {str(e)}")

    return  # 阻止自动回复


# Flask路由
@app.route('/')
def index():
    """渲染监控页面"""
    return render_template('index.html')


@app.route('/messages')
def get_messages():
    """获取所有聊天记录"""
    session = Session()
    messages = session.query(ChatMessage).order_by(ChatMessage.created_at.desc()).all()
    result = [{
        'id': msg.id,
        'sender_name': msg.sender_name,
        'message': msg.message,
        'reply': msg.reply,
        'created_at': msg.created_at.strftime('%Y-%m-%d %H:%M:%S')
    } for msg in messages]
    session.close()
    return {'messages': result}


def run_flask():
    """运行Flask应用"""
    app.config['SECRET_KEY'] = 'your-secret-key-here'
    app.config['TEMPLATES_AUTO_RELOAD'] = True
    app.run(
        host='127.0.0.1',
        port=5000,
        debug=False,
        threaded=True
    )


def open_dashboard():
    """打开监控面板"""
    time.sleep(2)
    webbrowser.open('http://127.0.0.1:5000')


def login_wechat():
    """微信登录函数"""
    try:
        if os.path.exists('itchat.pkl'):
            os.remove('itchat.pkl')
            logger.info("删除旧的登录状态文件")

        itchat.auto_login(
            hotReload=False,
            enableCmdQR=-2,
            statusStorageDir='itchat.pkl',
            loginCallback=lambda: logger.info("登录成功"),
            exitCallback=lambda: logger.info("微信退出")
        )

        time.sleep(3)

        friends = itchat.get_friends()
        if friends:
            logger.info(f"登录验证成功，共有 {len(friends)} 个好友")
            open_dashboard()
            return True

        logger.error("登录验证失败")
        return False

    except Exception as e:
        logger.error(f"登录过程出错: {str(e)}")
        return False


def main():
    """主函数"""
    try:
        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()
        logger.info("监控服务器已启动")

        retry_count = 0
        max_retries = 3

        while retry_count < max_retries:
            try:
                if login_wechat():
                    @itchat.msg_register([TEXT])
                    def text_reply(msg):
                        return handle_text(msg)

                    logger.info("开始运行微信机器人...")
                    itchat.run(debug=True)
                    break
                else:
                    retry_count += 1
                    if retry_count < max_retries:
                        logger.info(f"等待 10 秒后进行第 {retry_count + 1} 次重试")
                        time.sleep(10)
            except Exception as e:
                logger.error(f"运行出错: {str(e)}")
                retry_count += 1
                if retry_count < max_retries:
                    logger.info(f"等待 10 秒后进行第 {retry_count + 1} 次重试")
                    time.sleep(10)

        if retry_count >= max_retries:
            logger.error("多次尝试登录失败，程序退出")

    except Exception as e:
        logger.error(f"程序运行错误: {str(e)}")
    finally:
        logger.info("程序退出")


if __name__ == '__main__':
    try:
        if not hasattr(itchat, '__version__') or itchat.__version__ < '1.5.0':
            logger.warning("建议更新 itchat-uos 到最新版本")
        main()
    except KeyboardInterrupt:
        logger.info("程序被用户中断")
    except Exception as e:
        logger.error(f"程序异常退出: {str(e)}")