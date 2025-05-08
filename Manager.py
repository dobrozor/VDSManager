from flask import Flask, render_template_string, request, session, jsonify
import paramiko
import os
import uuid
import time
import re
from html import escape
from threading import Thread, Lock  # Исправленный импорт
import queue

app = Flask(__name__)
app.secret_key = os.urandom(24)
app.config['TEMPLATES_AUTO_RELOAD'] = True

# Конфигурация SSH
SSH_HOST = 'Ваш_айпи_хост'
SSH_PORT = 22
SSH_USERNAME = 'имя_пользователя'
SSH_PASSWORD = 'пароль_пользователя'

ssh_sessions = {}
output_queues = {}
session_lock = Lock()


def ansi_to_html(text):
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    cleaned = ansi_escape.sub('', text)
    return escape(cleaned)


def create_ssh_connection():
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(
        hostname=SSH_HOST,
        port=SSH_PORT,
        username=SSH_USERNAME,
        password=SSH_PASSWORD,
        timeout=30
    )
    return ssh


def execute_initial_commands(shell):
    commands = ['cd bots', 'source venv/bin/activate', 'cd']
    for cmd in commands:
        shell.send(cmd + '\n')
        time.sleep(0.5)
        while shell.recv_ready():
            shell.recv(65536)


def output_reader(session_id, shell):
    while True:
        with session_lock:
            if session_id not in ssh_sessions:
                break

        try:
            if shell.recv_ready():
                data = shell.recv(65536).decode('utf-8', 'ignore')
                with session_lock:
                    if session_id in output_queues:
                        output_queues[session_id].put(data)
            else:
                time.sleep(0.5)
        except (paramiko.SSHException, OSError):
            break


@app.route('/', methods=['GET', 'POST'])
def index():
    session_id = str(uuid.uuid4())
    try:
        ssh = create_ssh_connection()
        shell = ssh.invoke_shell(term='xterm', width=200, height=50)
        execute_initial_commands(shell)

        with session_lock:
            ssh_sessions[session_id] = {'ssh': ssh, 'shell': shell}
            output_queues[session_id] = queue.Queue()

        reader_thread = Thread(target=output_reader, args=(session_id, shell))
        reader_thread.daemon = True
        reader_thread.start()

        session['session_id'] = session_id
        return render_template_string(TEMPLATE.format(
            username=SSH_USERNAME,
            host=SSH_HOST,
            css_content=CSS,
            script_content=SCRIPT
        ))
    except Exception as e:
        return f"Connection Error: {str(e)}"


@app.route('/exec', methods=['POST'])
def exec_command():
    session_id = session.get('session_id')
    if not session_id or session_id not in ssh_sessions:
        return jsonify({'status': 'error', 'message': 'Session expired'}), 400

    command = request.form['command']
    shell = ssh_sessions[session_id]['shell']

    control_mappings = {
        'ctrl+z': '\x1a', 'ctrl+c': '\x03',
        'ctrl+d': '\x04', 'ctrl+l': '\x0c'
    }

    try:
        if command.lower() in control_mappings:
            shell.send(control_mappings[command.lower()])
            return '', 204  # No Content response for control commands
        else:
            shell.send(command + '\n')
            return jsonify({'status': 'success'})
    except (paramiko.SSHException, OSError) as e:
        return jsonify({
            'status': 'error',
            'message': f'Connection error: {str(e)}'
        }), 500


@app.route('/get_output', methods=['GET'])
def get_output():
    session_id = session.get('session_id')
    if not session_id or session_id not in ssh_sessions:
        return jsonify({'output': '', 'status': 'error'})

    output = []
    with session_lock:
        if session_id in output_queues:
            while not output_queues[session_id].empty():
                output.append(output_queues[session_id].get())

    processed = ''.join(output)
    processed = processed.replace('\r\n', '\n').replace('\r', '')
    return jsonify({
        'output': ansi_to_html(processed),
        'status': 'success'
    })


@app.route('/cleanup', methods=['POST'])
def cleanup():
    session_id = session.get('session_id')
    if session_id:
        with session_lock:
            if session_id in ssh_sessions:
                try:
                    ssh_sessions[session_id]['shell'].close()
                    ssh_sessions[session_id]['ssh'].close()
                except:
                    pass
                del ssh_sessions[session_id]
            if session_id in output_queues:
                del output_queues[session_id]
    return jsonify({'status': 'success'})


CSS = '''
:root {
    --bg-color: #FFFFFF;
    --terminal-bg: rgba(255, 255, 255, 0.9);
    --accent: #EA580C;
    --accent-light: #FDBA74;
    --text-primary: #1E293B;
    --text-secondary: #64748B;
    --border-color: #E2E8F0;
}

body {
    margin: 0;
    background: var(--bg-color);
    color: var(--text-primary);
    font-family: 'JetBrains Mono', monospace;
    line-height: 1.6;
}

.container {
    max-width: 1200px;
    margin: 0 auto;
    padding: 2rem;
}

.terminal-container {
    background: var(--terminal-bg);
    border-radius: 1rem;
    border: 1px solid var(--border-color);
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.1);
}

.terminal-header {
    padding: 1rem;
    border-bottom: 1px solid var(--border-color);
    display: flex;
    align-items: center;
    gap: 1rem;
}

.controls {
    display: flex;
    gap: 0.5rem;
}

.circle {
    width: 12px;
    height: 12px;
    border-radius: 50%;
}

.red { background: #FF605C; }
.yellow { background: #FFBD44; }
.green { background: #00CA4E; }

.title {
    color: var(--text-secondary);
    font-size: 0.9rem;
}

#output {
    height: 60vh;
    padding: 1.5rem;
    overflow-y: auto;
    margin: 0;
    white-space: pre-wrap;
    background: rgba(241, 245, 249, 0.5);
    color: var(--text-primary);
}

.input-area {
    padding: 1.5rem;
    border-top: 1px solid var(--border-color);
}

.input-container {
    display: flex;
    align-items: center;
    gap: 1rem;
}

.prompt {
    color: var(--accent);
    white-space: nowrap;
}

#command {
    flex: 1;
    background: none;
    border: 1px solid var(--border-color);
    color: var(--text-primary);
    font-family: 'JetBrains Mono', monospace;
    font-size: 1rem;
    padding: 0.5rem 1rem;
    border-radius: 0.5rem;
    background: rgba(241, 245, 249, 0.5);
}

#command:focus {
    outline: none;
    box-shadow: 0 0 0 2px var(--accent);
    border-color: var(--accent);
}

.control-buttons {
    display: flex;
    gap: 0.5rem;
    margin-left: auto;
}

.control-buttons button {
    background: rgba(241, 245, 249, 0.8);
    border: 1px solid var(--border-color);
    border-radius: 0.5rem;
    padding: 0.5rem;
    cursor: pointer;
    color: var(--text-secondary);
    display: flex;
    align-items: center;
    transition: all 0.2s;
}

.control-buttons button:hover {
    background: var(--accent);
    color: white;
    border-color: var(--accent);
}

.command-history-hint {
    font-size: 0.8rem;
    color: var(--text-secondary);
    margin-top: 0.5rem;
    text-align: right;
    padding-right: 0.5rem;
}

::-webkit-scrollbar {
    width: 8px;
}

::-webkit-scrollbar-track {
    background: rgba(0, 0, 0, 0.05);
}

::-webkit-scrollbar-thumb {
    background: rgba(234, 88, 12, 0.3);
    border-radius: 4px;
}

::-webkit-scrollbar-thumb:hover {
    background: var(--accent);
}
'''

SCRIPT = '''
let commandHistory = [];
let historyIndex = -1;
const MAX_HISTORY = 50;
let outputUpdateInterval;
let isCommandRunning = false;

const output = document.getElementById('output');
const commandInput = document.getElementById('command');

async function checkForOutput() {
    try {
        const response = await fetch('/get_output');
        const data = await response.json();

        if (data.status === 'success' && data.output) {
            output.innerHTML += data.output;
            output.scrollTop = output.scrollHeight;
        }
    } catch(error) {
        console.error('Error checking output:', error);
    }
}

async function sendCommand(e) {
    e.preventDefault();
    const cmd = commandInput.value.trim();
    if (!cmd) return;

    commandHistory.push(cmd);
    if (commandHistory.length > MAX_HISTORY) commandHistory.shift();
    historyIndex = -1;

    output.innerHTML += `<span style="color:#EA580C">$ ${cmd}</span>\n`;
    output.scrollTop = output.scrollHeight;

    isCommandRunning = true;
    commandInput.disabled = true;

    try {
        await fetch('/exec', {
            method: 'POST',
            headers: {'Content-Type': 'application/x-www-form-urlencoded'},
            body: 'command=' + encodeURIComponent(cmd)
        });

        // Интенсивный опрос в течение первых 5 секунд
        const intensiveCheck = setInterval(checkForOutput, 50);
        setTimeout(() => clearInterval(intensiveCheck), 5000);

        // Стандартный интервал опроса
        outputUpdateInterval = setInterval(checkForOutput, 200);

    } catch(error) {
        output.innerHTML += `<span style="color:#FF605C">✖ Error: ${error.message}</span>\n`;
    } finally {
        commandInput.value = '';
        commandInput.disabled = false;
        commandInput.focus();
    }
}


function init() {
    commandInput.focus();
    output.innerHTML = '<span style="color:#EA580C">🚀 Подключился к серверу</span>\\n';
    document.getElementById('commandForm').addEventListener('submit', sendCommand);
    commandInput.addEventListener('keydown', handleKeys);
    window.addEventListener('keydown', handleGlobalKeys);

    // Добавляем подсказку о навигации по истории
    const hint = document.createElement('div');
    hint.className = 'command-history-hint';
    hint.textContent = 'Используй PageUp/PageDown чтоб смотреть отправленные команды';
    document.querySelector('.input-area').appendChild(hint);
}

function handleKeys(e) {
    switch(e.key) {
        case 'PageUp':
            e.preventDefault();
            navigateHistory('up');
            break;
        case 'PageDown':
            e.preventDefault();
            navigateHistory('down');
            break;
        case 'ArrowUp':
            if (commandInput.selectionStart === 0) {
                e.preventDefault();
                navigateHistory('up');
            }
            break;
        case 'ArrowDown':
            if (commandInput.selectionStart === commandInput.value.length) {
                e.preventDefault();
                navigateHistory('down');
            }
            break;
    }
}

function handleGlobalKeys(e) {
    if (e.ctrlKey) {
        const keyMap = { 'z': 'ctrl+z', 'c': 'ctrl+c', 'd': 'ctrl+d', 'l': 'ctrl+l' };
        if (keyMap[e.key.toLowerCase()]) {
            e.preventDefault();
            sendControl(keyMap[e.key.toLowerCase()]);
        }
    }
}

function navigateHistory(direction) {
    if (commandHistory.length === 0) return;

    const newIndex = direction === 'up' ? historyIndex + 1 : historyIndex - 1;

    if (newIndex >= commandHistory.length) return;
    if (newIndex < -1) return;

    historyIndex = newIndex;
    commandInput.value = historyIndex > -1 
        ? commandHistory[commandHistory.length - 1 - historyIndex] 
        : '';

    // Помещаем курсор в конец строки
    commandInput.selectionStart = commandInput.selectionEnd = commandInput.value.length;
}

async function sendControl(cmd) {
    try {
        const response = await fetch('/exec', {
            method: 'POST',
            headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
            body: 'command=' + encodeURIComponent(cmd)
        });

        if (response.status === 204) {
            // Для управляющих команд просто добавляем сообщение в терминал
            output.innerHTML += `<span style="color:#EA580C">⚡ ${cmd.toUpperCase()} sent</span>\n`;
        } else if (!response.ok) {
            const error = await response.json();
            output.innerHTML += `<span style="color:#FF605C">✖ Error: ${error.message || 'Unknown error'}</span>\n`;
        }
        output.scrollTop = output.scrollHeight;
    } catch(error) {
        output.innerHTML += `<span style="color:#FF605C">✖ Error: ${error.message}</span>\n`;
        output.scrollTop = output.scrollHeight;
    }
}

init();
'''

TEMPLATE = '''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>WebSSH Terminal</title>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
    <style>{css_content}</style>
</head>
<body>
    <div class="container">
        <div class="terminal-container">
            <div class="terminal-header">
                <div class="controls">
                    <div class="circle red"></div>
                    <div class="circle yellow"></div>
                    <div class="circle green"></div>
                </div>
                <div class="title">ssh://{username}@{host}</div>
            </div>
            <pre id="output"></pre>
            <div class="input-area">
                <div class="input-container">
                    <div class="prompt">
                        <span class="user">➜</span>
                        <span class="path">@{username}</span>
                    </div>
                    <form id="commandForm" onsubmit="return false;">
                        <input type="text" id="command" 
                            autocomplete="off"
                            autocorrect="off"
                            autocapitalize="off"
                            spellcheck="false"
                            placeholder="Введи команду...">
                    </form>
                    <div class="control-buttons">
                        <button type="button" onclick="sendControl('ctrl+z')" title="Suspend (Ctrl+Z)">
                            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor">
                                <path d="M6 4v16M18 4v16"/>
                            </svg>
                        </button>
                        <button type="button" onclick="sendControl('ctrl+c')" title="Interrupt (Ctrl+C)">
                            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor">
                                <circle cx="12" cy="12" r="10"/>
                                <path d="M15 9l-6 6m0-6l6 6"/>
                            </svg>
                        </button>
                        <button type="button" onclick="sendControl('ctrl+d')" title="EOF (Ctrl+D)">
                            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor">
                                <path d="M18 6L6 18M6 6l12 12"/>
                            </svg>
                        </button>
                        <button type="button" onclick="sendControl('ctrl+l')" title="Clear (Ctrl+L)">
                            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor">
                                <path d="M3 6h18M7 12h10M5 18h14"/>
                            </svg>
                        </button>
                    </div>
                </div>
            </div>
        </div>
    </div>
    <script>{script_content}</script>
</body>
</html>
'''

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
