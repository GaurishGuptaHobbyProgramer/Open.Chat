const randomID = Math.floor(Math.random() * 9000) + 1000;

let usedBackgroundSeeds = [];

function buildRandomBackgroundImage(seed) {
    const imageUrl = `https://picsum.photos/seed/${seed}/1800/1200`;
    return `linear-gradient(135deg, rgba(8, 15, 29, 0.68), rgba(21, 41, 70, 0.42)), url("${imageUrl}")`;
}

function generateBackgroundSeed() {
    const randomSeed = `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    if (usedBackgroundSeeds.includes(randomSeed)) {
        return generateBackgroundSeed();
    }
    usedBackgroundSeeds.push(randomSeed);
    if (usedBackgroundSeeds.length > 20) {
        usedBackgroundSeeds = usedBackgroundSeeds.slice(-20);
    }
    return randomSeed;
}

function rotateLiveBackground() {
    const nextSeed = generateBackgroundSeed();
    const nextBackground = buildRandomBackgroundImage(nextSeed);
    document.documentElement.style.setProperty("--live-background-image", nextBackground);
    document.body.style.backgroundImage = nextBackground;
}

setInterval(rotateLiveBackground, 5000);

if (document.readyState === "complete") {
    rotateLiveBackground();
} else {
    window.addEventListener("load", rotateLiveBackground, { once: true });
}

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

const username = document.body.dataset.currentUsername || storedUser || `Anonymous ${randomID}`;

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

function renderAttachmentMarkup(data) {
    const attachmentName = data.attachment_name || "Attachment";
    const attachmentType = data.attachment_type || "";
    const attachmentUrl = data.attachment_url || "";

    if (!attachmentUrl) {
        return "";
    }

    if (attachmentType.startsWith("image/")) {
        return `
            <div class="attachment attachment-image">
                <img src="${attachmentUrl}" alt="${escapeHTML(attachmentName)}" />
            </div>
            <a class="attachment-link" href="${attachmentUrl}" target="_blank" rel="noopener noreferrer">${escapeHTML(attachmentName)}</a>
        `;
    }

    if (attachmentType.startsWith("video/")) {
        return `
            <div class="attachment attachment-video">
                <video controls src="${attachmentUrl}"></video>
            </div>
            <a class="attachment-link" href="${attachmentUrl}" target="_blank" rel="noopener noreferrer">${escapeHTML(attachmentName)}</a>
        `;
    }

    return `
        <div class="attachment attachment-file">
            <a class="attachment-link" href="${attachmentUrl}" target="_blank" rel="noopener noreferrer">${escapeHTML(attachmentName)}</a>
        </div>
    `;
}

function appendMessage(data, isOwnMessage = false) {
    const safeUsername = data.username || "Unknown";
    const initials = safeUsername.split(" ").pop().slice(0, 2).toUpperCase() || "?";
    const timestamp = formatDeviceTimestamp(data.created_at || data.time || new Date());
    const messageText = data.message ? escapeHTML(data.message) : "";
    const attachmentMarkup = renderAttachmentMarkup(data);
    const messageElement = document.createElement("div");
    messageElement.className = `message ${isOwnMessage ? "own" : ""}`;

    messageElement.innerHTML = `
        <div class="avatar">${escapeHTML(initials)}</div>
        <div class="message-content">
            <div class="message-header">
                <b>${escapeHTML(safeUsername)}</b>
                <span>${escapeHTML(timestamp)}</span>
            </div>
            <div class="message-body">
                ${messageText ? `<div class="message-text">${messageText}</div>` : ""}
                ${attachmentMarkup}
            </div>
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

async function sendMessage(message, file) {
    const formData = new FormData();
    if (message) {
        formData.append("message", message);
    }
    if (file) {
        formData.append("attachment", file);
    }

    const response = await fetch("/messages", {
        method: "POST",
        body: formData,
        cache: "no-store"
    });

    if (!response.ok) {
        throw new Error("Send failed");
    }

    const payload = await response.json();
    appendMessage(payload, true);
    lastMessageId = Math.max(lastMessageId || 0, Number(payload.id) || 0);
}

const attachmentInput = document.getElementById("attachment-input");

if (messageForm) {
    messageForm.addEventListener("submit", async function (event) {
        event.preventDefault();

        const message = messageInput.value.trim();
        const file = attachmentInput && attachmentInput.files ? attachmentInput.files[0] : null;

        if (!message && !file) return;

        messageInput.value = "";
        if (attachmentInput) {
            attachmentInput.value = "";
        }
        messageInput.focus();
        try {
            await sendMessage(message, file);
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

const aiBadge = document.querySelector(".ai-floating-panel");

if (aiBadge) {
    let isDragging = false;
    let dragOffsetX = 0;
    let dragOffsetY = 0;

    function updateBadgePosition(x, y) {
        const maxX = window.innerWidth - aiBadge.offsetWidth - 12;
        const maxY = window.innerHeight - aiBadge.offsetHeight - 12;
        const nextX = Math.min(Math.max(x, 12), maxX);
        const nextY = Math.min(Math.max(y, 12), maxY);
        aiBadge.style.left = `${nextX}px`;
        aiBadge.style.top = `${nextY}px`;
        aiBadge.style.right = "auto";
        aiBadge.style.bottom = "auto";
    }

    aiBadge.addEventListener("pointerdown", (event) => {
        const rect = aiBadge.getBoundingClientRect();
        dragOffsetX = event.clientX - rect.left;
        dragOffsetY = event.clientY - rect.top;
        isDragging = true;
        aiBadge.setPointerCapture(event.pointerId);
        aiBadge.style.transition = "none";
    });

    aiBadge.addEventListener("pointermove", (event) => {
        if (!isDragging) return;
        updateBadgePosition(event.clientX - dragOffsetX, event.clientY - dragOffsetY);
    });

    aiBadge.addEventListener("pointerup", () => {
        isDragging = false;
        aiBadge.style.transition = "transform 0.15s ease";
    });

    aiBadge.addEventListener("pointerleave", () => {
        isDragging = false;
        aiBadge.style.transition = "transform 0.15s ease";
    });

    const storedBadgePosition = localStorage.getItem("openchat-ai-badge-position");
    if (storedBadgePosition) {
        try {
            const position = JSON.parse(storedBadgePosition);
            requestAnimationFrame(() => updateBadgePosition(position.x || 20, position.y || 20));
        } catch (error) {
            console.error("Failed to restore AI badge position:", error);
        }
    }

    window.addEventListener("beforeunload", () => {
        const rect = aiBadge.getBoundingClientRect();
        localStorage.setItem("openchat-ai-badge-position", JSON.stringify({
            x: rect.left,
            y: rect.top,
        }));
    });
}

startPolling();

