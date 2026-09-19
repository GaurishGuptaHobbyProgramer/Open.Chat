const randomID = Math.floor(Math.random() * 9000) + 1000;

function detectDeviceTypeFromUserAgent() {
    return /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent)
        || (window.matchMedia && window.matchMedia("(max-width: 640px)").matches)
        ? "mobile"
        : "desktop";
}

const savedDeviceMode = localStorage.getItem("openchat-device-mode");
const initialDeviceType = savedDeviceMode || detectDeviceTypeFromUserAgent();
const deviceType = initialDeviceType === "mobile" ? "mobile" : "desktop";

document.body.classList.add(deviceType === "mobile" ? "device-mobile" : "device-desktop");
document.body.dataset.deviceType = deviceType;

const storedUser = localStorage.getItem("openchat-username");
const storedToken = localStorage.getItem("openchat-user-token") || (
    (typeof crypto !== "undefined" && crypto.randomUUID)
        ? crypto.randomUUID()
        : `client-${Date.now()}-${randomID}`
);
const userToken = storedToken;

localStorage.setItem("openchat-user-token", userToken);

const usernameInput = document.getElementById("username-input");
let username = storedUser || `Anonymous ${randomID}`;

const messageForm = document.getElementById("message-form");
const messageInput = document.getElementById("message-input");
const chatBox = document.getElementById("chat-box");
const onlineUsers = document.getElementById("online-users");
const themeToggle = document.getElementById("theme-toggle");
const deviceModeToggle = document.getElementById("device-mode-toggle");

function applyDeviceMode(mode) {
    const nextMode = mode === "mobile" ? "mobile" : "desktop";
    document.body.classList.remove("device-mobile", "device-desktop");
    document.body.classList.add(nextMode === "mobile" ? "device-mobile" : "device-desktop");
    document.body.dataset.deviceType = nextMode;
    localStorage.setItem("openchat-device-mode", nextMode);

    if (deviceModeToggle) {
        deviceModeToggle.textContent = nextMode === "mobile" ? "Desktop mode" : "Mobile mode";
        deviceModeToggle.setAttribute("aria-label", nextMode === "mobile" ? "Switch to desktop mode" : "Switch to mobile mode");
    }
}

if (usernameInput) {
    usernameInput.value = username;
    usernameInput.addEventListener("input", () => {
        username = usernameInput.value.trim() || `Anonymous ${randomID}`;
        localStorage.setItem("openchat-username", username);
    });
}

function applyTheme(theme) {
    document.documentElement.classList.remove("light-theme", "dark-theme");
    document.documentElement.classList.add(theme === "light" ? "light-theme" : "dark-theme");
    if (themeToggle) {
        themeToggle.setAttribute("aria-pressed", theme === "light" ? "true" : "false");
    }
}

const savedTheme = localStorage.getItem("openchat-theme");
if (savedTheme) {
    applyTheme(savedTheme);
} else {
    const prefersLight = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches;
    applyTheme(prefersLight ? "light" : "dark");
}

if (themeToggle) {
    themeToggle.addEventListener("click", () => {
        const isLight = document.documentElement.classList.contains("light-theme");
        const nextTheme = isLight ? "dark" : "light";
        applyTheme(nextTheme);
        localStorage.setItem("openchat-theme", nextTheme);
    });
}

if (deviceModeToggle) {
    deviceModeToggle.addEventListener("click", () => {
        const nextMode = document.body.classList.contains("device-mobile") ? "desktop" : "mobile";
        applyDeviceMode(nextMode);
    });
}

applyDeviceMode(deviceType);

function escapeHTML(str) {
    if (!str) return "";
    return String(str).replace(/[&<>"']/g, (tag) => {
        const chars = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
        return chars[tag] || tag;
    });
}

function formatDeviceTimestamp(value) {
    if (!value) {
        const now = new Date();
        return new Intl.DateTimeFormat(undefined, {
            day: "2-digit",
            month: "2-digit",
            year: "numeric",
            hour: "2-digit",
            minute: "2-digit",
            hour12: true,
        }).format(now);
    }

    const rawValue = typeof value === "string" ? value.trim() : value;
    const dateCandidates = [];

    if (typeof rawValue === "string") {
        dateCandidates.push(rawValue);
        dateCandidates.push(rawValue.replace(" ", "T"));
        if (!rawValue.endsWith("Z") && /\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}/.test(rawValue)) {
            dateCandidates.push(`${rawValue.endsWith("T") ? rawValue : rawValue.replace(" ", "T")}Z`);
        }
    } else {
        dateCandidates.push(rawValue);
    }

    for (const candidate of dateCandidates) {
        const parsedDate = new Date(candidate);
        if (!Number.isNaN(parsedDate.getTime())) {
            return new Intl.DateTimeFormat(undefined, {
                day: "2-digit",
                month: "2-digit",
                year: "numeric",
                hour: "2-digit",
                minute: "2-digit",
                hour12: true,
            }).format(parsedDate);
        }
    }

    return rawValue;
}

function appendMessage(data, isOwnMessage = false) {
    const safeUsername = data.username || "Unknown";
    const initials = safeUsername.split(" ").pop().slice(0, 2).toUpperCase() || "?";
    const timestamp = formatDeviceTimestamp(data.created_at || data.time || new Date());
    const messageElement = document.createElement("div");
    messageElement.className = `message ${isOwnMessage ? "own" : ""}`;

    messageElement.innerHTML = `
        <div class="avatar">${escapeHTML(initials)}</div>
        <div class="message-content">
            <div class="message-header">
                <b>${escapeHTML(safeUsername)}</b>
                <span>${escapeHTML(timestamp)}</span>
            </div>
            <div class="message-body">${escapeHTML(data.message || "")}</div>
        </div>
    `;

    chatBox.appendChild(messageElement);
    chatBox.scrollTop = chatBox.scrollHeight;
}

let lastRenderedSignature = "";
let lastMessageId = null;
let pollingTimer = null;

function renderHistory(messages) {
    const nextSignature = JSON.stringify(messages || []);
    if (nextSignature === lastRenderedSignature) {
        return;
    }

    lastRenderedSignature = nextSignature;
    chatBox.innerHTML = "";
    messages.forEach((message) => {
        appendMessage(message, message.username === username);
    });
    if (messages.length) {
        lastMessageId = Math.max(...messages.map((message) => Number(message.id) || 0));
    }
    chatBox.scrollTop = chatBox.scrollHeight;
}

function appendIncomingMessages(messages) {
    if (!messages.length) return;

    let highestId = lastMessageId || 0;
    messages.forEach((message) => {
        const messageId = Number(message.id) || 0;
        if (messageId <= highestId) return;

        appendMessage(message, message.username === username);
        highestId = messageId;
    });

    lastMessageId = highestId;
    chatBox.scrollTop = chatBox.scrollHeight;
}

async function loadMessages() {
    try {
        const url = lastMessageId ? `/messages?since_id=${lastMessageId}` : "/messages?limit=80";
        const response = await fetch(url, { cache: "no-store" });
        const messages = await response.json();

        if (!messages.length) {
            return;
        }

        if (lastMessageId === null) {
            renderHistory(messages);
        } else {
            appendIncomingMessages(messages);
        }
    } catch (error) {
        console.error("Failed to load messages:", error);
    }
}

async function updatePresence() {
    try {
        const response = await fetch("/presence", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ token: userToken }),
            cache: "no-store"
        });
        const data = await response.json();
        if (onlineUsers) {
            onlineUsers.textContent = data.count === 1 ? "🟢 1 online" : `🟢 ${data.count} online`;
        }
    } catch (error) {
        console.error("Failed to update presence:", error);
    }
}

async function sendMessage(message) {
    if (!message) return;

    const response = await fetch("/messages", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, message }),
        cache: "no-store"
    });

    if (!response.ok) {
        throw new Error("Send failed");
    }

    const payload = await response.json();
    appendMessage(payload, true);
    lastMessageId = Math.max(lastMessageId || 0, Number(payload.id) || 0);
}

if (messageForm) {
    messageForm.addEventListener("submit", async function (event) {
        event.preventDefault();

        const message = messageInput.value.trim();
        if (!message) return;

        messageInput.value = "";
        messageInput.focus();
        try {
            await sendMessage(message);
        } catch (error) {
            console.error("Message send error:", error);
        }
    });
}

window.addEventListener("beforeunload", () => {
    localStorage.setItem("openchat-username", username);
    fetch("/presence", {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: userToken })
    }).catch(() => {});
});

localStorage.setItem("openchat-username", username);

async function startPolling() {
    if (document.hidden) {
        return;
    }

    await loadMessages();
    await updatePresence();
    pollingTimer = setTimeout(startPolling, 1200);
}

if (typeof document !== "undefined") {
    document.addEventListener("visibilitychange", () => {
        if (!document.hidden && !pollingTimer) {
            startPolling();
        }
    });
}

startPolling();

