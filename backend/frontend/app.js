// ===== CONFIG: change if backend runs elsewhere =====
const HTTP_BASE = "http://127.0.0.1:8000";
const WS_BASE   = "ws://127.0.0.1:8000";

// ===== Elements =====
const loginCard = document.getElementById("login-card");
const chatView  = document.getElementById("chat-view");

const usernameEl = document.getElementById("username");
const roomEl     = document.getElementById("room");
const joinBtn    = document.getElementById("join");

const roomTitle  = document.getElementById("room-title");
const typingEl   = document.getElementById("typing");
const messagesEl = document.getElementById("messages");
const onlineEl   = document.getElementById("online");
const textEl     = document.getElementById("text");
const sendBtn    = document.getElementById("send");

let ws = null;
let state = {
  username: null,
  room: null,
  typingTimeout: null
};

// ===== Helpers =====
function el(tag, className, text) {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text) e.textContent = text;
  return e;
}

function addMessage({ username, content, timestamp }) {
  const row = el("div", "message");
  const meta = el("div", "meta", `${username} • ${new Date(timestamp).toLocaleTimeString()}`);
  const body = el("div", "body", content);
  row.appendChild(meta);
  row.appendChild(body);
  messagesEl.appendChild(row);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function setOnline(users) {
  onlineEl.textContent = users.join(", ");
}

function showTyping(name, isTyping) {
  if (isTyping) {
    typingEl.textContent = `${name} is typing…`;
    clearTimeout(state.typingTimeout);
    state.typingTimeout = setTimeout(() => {
      typingEl.textContent = "";
    }, 1500);
  } else {
    typingEl.textContent = "";
  }
}

// ===== API calls =====
async function getRooms() {
  const res = await fetch(`${HTTP_BASE}/rooms`);
  const data = await res.json();
  return data.rooms || [];
}

async function login(username) {
  const res = await fetch(`${HTTP_BASE}/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username })
  });
  if (!res.ok) throw new Error("Login failed");
  return await res.json();
}

async function getHistory(room) {
  const res = await fetch(`${HTTP_BASE}/messages?room=${encodeURIComponent(room)}&limit=50`);
  if (!res.ok) return { messages: [] };
  return await res.json();
}

// ===== WebSocket =====
function connectWS(username, room) {
  ws = new WebSocket(`${WS_BASE}/ws/${encodeURIComponent(room)}?username=${encodeURIComponent(username)}`);

  ws.onopen = () => {
    console.log("WS connected");
  };

  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    // console.log("WS msg", msg);

    if (msg.type === "chat") {
      addMessage({ username: msg.username, content: msg.content, timestamp: msg.timestamp });
    } else if (msg.type === "join") {
      const row = el("div", "sys", `${msg.username} joined ${msg.room}`);
      messagesEl.appendChild(row);
    } else if (msg.type === "leave") {
      const row = el("div", "sys", `${msg.username} left ${msg.room}`);
      messagesEl.appendChild(row);
    } else if (msg.type === "online_list") {
      setOnline(msg.users || []);
    } else if (msg.type === "typing") {
      if (msg.username !== state.username) {
        showTyping(msg.username, msg.isTyping);
      }
    }
    messagesEl.scrollTop = messagesEl.scrollHeight;
  };

  ws.onclose = () => {
    console.log("WS closed");
  };
}

function sendChat(content) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  ws.send(JSON.stringify({ type: "chat", content }));
}

function sendTyping(isTyping) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  ws.send(JSON.stringify({ type: "typing", isTyping }));
}

// ===== UI Handlers =====
joinBtn.addEventListener("click", async () => {
  const username = usernameEl.value.trim();
  const room = roomEl.value;

  if (!username) {
    alert("Please enter a username");
    return;
  }
  try {
    await login(username);
  } catch (e) {
    alert("Login failed");
    return;
  }

  state.username = username;
  state.room = room;

  // load history
  messagesEl.innerHTML = "";
  const hist = await getHistory(room);
  (hist.messages || []).forEach(addMessage);

  // show chat view
  roomTitle.textContent = `# ${room}`;
  loginCard.classList.add("hidden");
  chatView.classList.remove("hidden");

  // connect WS
  connectWS(username, room);
});

sendBtn.addEventListener("click", () => {
  const text = textEl.value.trim();
  if (!text) return;
  sendChat(text);
  textEl.value = "";
});

textEl.addEventListener("input", () => {
  sendTyping(true);
  if (state.typingTimeout) clearTimeout(state.typingTimeout);
  state.typingTimeout = setTimeout(() => sendTyping(false), 800);
});

// Load rooms at startup
(async function init() {
  try {
    const rooms = await getRooms();
    rooms.forEach((r) => {
      const opt = document.createElement("option");
      opt.value = r;
      opt.textContent = r;
      roomEl.appendChild(opt);
    });
  } catch (e) {
    console.error("Failed to load rooms", e);
  }
})();
