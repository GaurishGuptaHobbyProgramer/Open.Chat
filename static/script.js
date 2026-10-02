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
const currentUserId = Number(document.body.dataset.currentUserId || "0") || null;

const messageForm = document.getElementById("message-form");
const messageInput = document.getElementById("message-input");
const chatBox = document.getElementById("chat-box");
const onlineUsers = document.getElementById("online-users");
let typingTimer = null;
let lastTypingState = false;
const themeToggle = document.getElementById("theme-toggle");
const deviceModeToggle = document.getElementById("device-mode-toggle");
const chatSearch = document.getElementById("chat-search");
const comingSoonToast = document.getElementById("coming-soon-toast");
const savedConvoId = sessionStorage.getItem("openchat-selected-conversation-id");
let selectedConversationId = savedConvoId ? Number(savedConvoId) : 1;
let selectedConversationName = "ENORMOUS";

function ensureActionModal() {
    let modal = document.getElementById("openchat-action-modal");
    if (modal) {
        return modal;
    }

    modal = document.createElement("div");
    modal.id = "openchat-action-modal";
    modal.className = "openchat-modal";
    modal.innerHTML = `
        <div class="openchat-modal-backdrop" data-close-modal="true"></div>
        <div class="openchat-modal-panel" role="dialog" aria-modal="true" aria-labelledby="openchat-modal-title">
            <h3 id="openchat-modal-title">Open.Chat</h3>
            <div id="openchat-modal-body"></div>
        </div>
    `;
    document.body.appendChild(modal);
    modal.addEventListener("click", (event) => {
        if (event.target.dataset.closeModal === "true") {
            modal.classList.remove("visible");
        }
    });
    return modal;
}

function showActionModal(mode) {
    const modal = ensureActionModal();
    const body = modal.querySelector("#openchat-modal-body");
    const title = modal.querySelector("#openchat-modal-title");
    if (!body || !title) {
        return;
    }

    if (mode === "new-chat") {
        title.textContent = "Start a new chat";
        body.innerHTML = `
            <input id="openchat-contact-search" type="text" placeholder="Search contacts by name or username" autocomplete="off" />
            <div class="search-results" id="openchat-contact-results"></div>
            <div class="modal-actions">
                <button type="button" class="secondary-modal-btn" data-close-modal="true">Close</button>
            </div>
        `;
        const searchInput = document.getElementById("openchat-contact-search");
        const results = document.getElementById("openchat-contact-results");
        const loadResults = async (value = "") => {
            const query = (value || "").trim();
            try {
                const response = await fetch(`/api/users/search?q=${encodeURIComponent(query)}`, { cache: "no-store" });
                const data = response.ok ? await response.json() : [];
                const filtered = (Array.isArray(data) ? data : []).filter((user) => user && user.username && Number(user.id) !== currentUserId);
                results.innerHTML = filtered.length
                    ? filtered.map((user) => `
                        <div class="contact-result">
                            <div class="contact-person">
                                <div class="contact-avatar">${buildAvatarMarkup(user.avatar_url, user.display_name || user.username)}</div>
                                <div class="contact-meta">
                                    <strong>${escapeHTML(user.display_name || user.username)}</strong>
                                    <small>${escapeHTML(user.username)}</small>
                                </div>
                            </div>
                            <button type="button" data-user-id="${user.user_id || user.id}" data-avatar-url="${escapeHTML(user.avatar_url || "")}" data-quick-action="start-chat">Open</button>
                        </div>
                    `).join("")
                    : '<div class="contact-result"><div class="contact-meta"><strong>No contacts found</strong><small>Search by name or username</small></div></div>';
                results.querySelectorAll("[data-quick-action='start-chat']").forEach((button) => {
                    button.addEventListener("click", async () => {
                        const userId = Number(button.dataset.userId);
                        try {
                            const response = await fetch("/api/conversations/start", {
                                method: "POST",
                                headers: { "Content-Type": "application/json" },
                                body: JSON.stringify({ user_id: userId }),
                                cache: "no-store"
                            });
                            const payload = await response.json();
                            if (!response.ok || !payload.conversation_id) {
                                throw new Error(payload.error || "Unable to start chat");
                            }
                            modal.classList.remove("visible");
                            await loadConversationList();
                            await selectConversation(payload.conversation_id, button.closest(".contact-result")?.querySelector("strong")?.textContent || "Direct chat", "Direct message", button.dataset.avatarUrl || "");

                        } catch (error) {
                            console.error("Start chat failed:", error);
                        }
                    });
                });
            } catch (error) {
                console.error("Failed to load contacts:", error);
                results.innerHTML = '<div class="contact-result"><div class="contact-meta"><strong>Search unavailable</strong><small>Please try again.</small></div></div>';
            }
        };

        searchInput.addEventListener("input", () => loadResults(searchInput.value));
        loadResults();
    }

    if (mode === "new-group") {
        title.textContent = "Create a group";
        body.innerHTML = `
            <input id="openchat-group-name" type="text" placeholder="Group name" value="New group" />
            <input id="openchat-group-search" type="text" placeholder="Search contacts to add" style="margin-top:12px;" autocomplete="off" />
            <div class="search-results" id="openchat-group-results"></div>
            <div class="modal-actions">
                <button type="button" class="secondary-modal-btn" data-close-modal="true">Cancel</button>
                <button type="button" class="modal-action-btn" id="openchat-create-group">Create group</button>
            </div>
        `;

        const groupNameInput = document.getElementById("openchat-group-name");
        const groupSearch = document.getElementById("openchat-group-search");
        const groupResults = document.getElementById("openchat-group-results");
        const selectedMembers = new Set();

        const renderGroupResults = async (value = "") => {
            const query = (value || "").trim();
            try {
                const response = await fetch(`/api/users/search?q=${encodeURIComponent(query)}`, { cache: "no-store" });
                const data = response.ok ? await response.json() : [];
                const filtered = (Array.isArray(data) ? data : []).filter((user) => user && user.username && user.username !== username);
                groupResults.innerHTML = filtered.length
                    ? filtered.map((user) => `
                        <div class="contact-result">
                            <div class="contact-meta">
                                <strong>${escapeHTML(user.display_name || user.username)}</strong>
                                <small>${escapeHTML(user.username)}</small>
                            </div>
                            <input type="checkbox" data-member-id="${user.user_id || user.id}" ${selectedMembers.has(String(user.user_id || user.id)) ? "checked" : ""} />
                        </div>
                    `).join("")
                    : '<div class="contact-result"><div class="contact-meta"><strong>No contacts found</strong><small>Search by name or username</small></div></div>';
                groupResults.querySelectorAll("input[type='checkbox']").forEach((checkbox) => {
                    checkbox.addEventListener("change", (event) => {
                        const userId = String(event.target.dataset.memberId);
                        if (event.target.checked) {
                            selectedMembers.add(userId);
                        } else {
                            selectedMembers.delete(userId);
                        }
                    });
                });
            } catch (error) {
                console.error("Failed to load searchable contacts:", error);
            }
        };

        groupSearch.addEventListener("input", () => renderGroupResults(groupSearch.value));
        document.getElementById("openchat-create-group").addEventListener("click", async () => {
            const members = Array.from(selectedMembers).map((value) => Number(value)).filter((value) => Number.isFinite(value));
            const titleText = (groupNameInput.value || "New group").trim() || "New group";
            try {
                const response = await fetch("/api/groups/create", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ title: titleText, members }),
                    cache: "no-store"
                });
                const payload = await response.json();
                if (!response.ok || !payload.conversation_id) {
                    throw new Error(payload.error || "Unable to create group");
                }
                modal.classList.remove("visible");
                await loadConversationList();
                await selectConversation(payload.conversation_id, titleText, "Group chat");

            } catch (error) {
                console.error("Group creation failed:", error);
            }
        });

        renderGroupResults();
    }

    if (mode === "status") {
        title.textContent = "Update status";
        body.innerHTML = `
            <textarea id="openchat-status-input" placeholder="What is happening today?"></textarea>
            <div class="modal-actions">
                <button type="button" class="secondary-modal-btn" data-close-modal="true">Cancel</button>
                <button type="button" class="modal-action-btn" id="openchat-save-status">Share</button>
            </div>
        `;
        const statusInput = document.getElementById("openchat-status-input");
        document.getElementById("openchat-save-status").addEventListener("click", async () => {
            const text = (statusInput.value || "").trim();
            if (!text) {
                return;
            }
            try {
                const response = await fetch("/api/statuses", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ text }),
                    cache: "no-store"
                });
                const payload = await response.json();
                if (!response.ok) {
                    throw new Error(payload.error || "Unable to save status");
                }
                modal.classList.remove("visible");
            } catch (error) {
                console.error("Status save failed:", error);
            }
        });
    }

    if (mode === "calls") {
        title.textContent = "Voice and video calls";
        body.innerHTML = `
            <p class="feature-state-copy"><strong>Under Development</strong></p>
            <p>Calling is not available yet. No call session will be started.</p>
            <div class="modal-actions"><button type="button" class="secondary-modal-btn" data-close-modal="true">Close</button></div>
        `;
    }

    modal.classList.add("visible");
}

function showComingSoonToast() {
    if (!comingSoonToast) {
        return;
    }

    comingSoonToast.classList.add("visible");
    clearTimeout(showComingSoonToast.timeout);
    showComingSoonToast.timeout = setTimeout(() => {
        comingSoonToast.classList.remove("visible");
    }, 1800);
}

if (document.querySelector(".ai-trigger")) {
    document.querySelectorAll(".ai-trigger").forEach((link) => {
        link.addEventListener("click", (event) => {
            event.preventDefault();
            showComingSoonToast();
            setTimeout(() => {
                window.location.href = link.getAttribute("href") || "/idle-ai";
            }, 500);
        });
    });
}

if (chatSearch) {
    chatSearch.addEventListener("input", () => {
        const searchTerm = chatSearch.value.trim().toLowerCase();
        document.querySelectorAll(".chat-item").forEach((item) => {
            const label = (item.textContent || "").toLowerCase();
            item.style.display = label.includes(searchTerm) ? "flex" : "none";
        });
    });
}

const quickActionButtons = document.querySelectorAll("[data-action]");
quickActionButtons.forEach((button) => {
    button.addEventListener("click", () => {
        const action = button.dataset.action;
        if (action === "new-chat") {
            showActionModal("new-chat");
        } else if (action === "new-group") {
            showActionModal("new-group");
        } else if (action === "status") {
            showActionModal("status");
        } else if (action === "calls") {
            showActionModal("calls");
        }
    });
});

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

function buildAvatarMarkup(avatarUrl, displayName) {
    if (avatarUrl) {
        return `<img src="${escapeHTML(avatarUrl)}" alt="" />`;
    }
    const initials = (displayName || "?").split(" ").map((part) => part[0]).slice(0, 2).join("").toUpperCase() || "?";
    return escapeHTML(initials);
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
    const safeUsername = isOwnMessage ? username : (data.sender_name || data.username || "Chat Partner");
    const initials = (safeUsername || "?").split(" ").map(w => w[0]).slice(0, 2).join("").toUpperCase() || "?";
    const timestamp = formatDeviceTimestamp(data.created_at || data.time || new Date());
    const contentText = data.text || data.message || "";
    const messageText = contentText ? escapeHTML(contentText) : "";
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
    (messages || []).forEach((message) => {
        const isOwn = currentUserId ? (message.sender_id === currentUserId) : (message.username === username);
        appendMessage(message, isOwn);
    });
    if (messages && messages.length) {
        lastMessageId = Math.max(...messages.map((message) => Number(message.id) || 0));
    }
    chatBox.scrollTop = chatBox.scrollHeight;
}

function appendIncomingMessages(messages) {
    if (!messages || !messages.length) return;

    let highestId = lastMessageId || 0;
    messages.forEach((message) => {
        const messageId = Number(message.id) || 0;
        if (messageId <= highestId) return;

        const isOwn = currentUserId ? (message.sender_id === currentUserId) : (message.username === username);
        appendMessage(message, isOwn);
        highestId = messageId;
    });

    lastMessageId = highestId;
    chatBox.scrollTop = chatBox.scrollHeight;
}

async function loadActiveConversationMessages(isInitial = false) {
    if (!selectedConversationId) {
        return;
    }

    const currentConvoId = selectedConversationId;
    try {
        let url = `/api/conversations/${currentConvoId}/messages?limit=100`;
        if (!isInitial && lastMessageId !== null) {
            url += `&since_id=${lastMessageId}`;
        }
        const response = await fetch(url, { cache: "no-store" });
        if (!response.ok) {
            return;
        }

        // Avoid race conditions if user switched active conversation while fetch was running
        if (selectedConversationId !== currentConvoId) {
            return;
        }

        const messages = await response.json();
        if (!Array.isArray(messages)) {
            return;
        }

        if (isInitial || lastMessageId === null) {
            renderHistory(messages);
        } else if (messages.length > 0) {
            appendIncomingMessages(messages);
        }
    } catch (error) {
        console.error("Failed to load conversation messages:", error);
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

async function setTypingState(isTyping) {
    if (!selectedConversationId || !currentUserId) {
        return;
    }

    if (lastTypingState === isTyping) {
        return;
    }

    lastTypingState = isTyping;
    try {
        await fetch(`/api/conversations/${selectedConversationId}/typing`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ is_typing: isTyping }),
            cache: "no-store",
        });
    } catch (error) {
        console.error("Failed to sync typing state:", error);
    }
}

async function refreshTypingState() {
    if (!selectedConversationId || !currentUserId) {
        return;
    }

    try {
        const response = await fetch(`/api/conversations/${selectedConversationId}/typing`, { cache: "no-store" });
        if (!response.ok) {
            return;
        }
        const data = await response.json();
        const typingUsers = Array.isArray(data.typing) ? data.typing : [];
        const titleNode = document.querySelector(".conversation-name");
        if (!titleNode || !typingUsers.length) {
            return;
        }
        const userName = typingUsers[0].user_id ? `User ${typingUsers[0].user_id}` : "Someone";
        titleNode.setAttribute("data-typing-text", `${userName} is typing...`);
        const metaNode = document.querySelector(".conversation-meta-row");
        if (metaNode) {
            metaNode.innerHTML = `<span class="online-dot"></span>${userName} is typing...`;
        }
    } catch (error) {
        console.error("Failed to fetch typing state:", error);
    }
}

async function loadNotifications() {
    if (!currentUserId) {
        return;
    }

    let notificationButton = document.getElementById("openchat-notifications");
    if (!notificationButton) {
        const quickActions = document.querySelector(".quick-actions");
        if (!quickActions) {
            return;
        }
        notificationButton = document.createElement("button");
        notificationButton.type = "button";
        notificationButton.id = "openchat-notifications";
        notificationButton.className = "nav-action";
        notificationButton.innerHTML = '🔔 <span class="notification-count">0</span>';
        notificationButton.title = "Notifications";
        quickActions.appendChild(notificationButton);
    }

    try {
        const response = await fetch("/api/notifications?unread_only=1&limit=10", { cache: "no-store" });
        if (!response.ok) {
            return;
        }

        const notifications = await response.json();
        const count = Array.isArray(notifications) ? notifications.length : 0;
        const badge = notificationButton.querySelector(".notification-count");
        if (badge) {
            badge.textContent = String(count);
            badge.style.display = count > 0 ? "inline-flex" : "none";
        }
        notificationButton.title = count > 0 ? `${count} unread notifications` : "No unread notifications";
    } catch (error) {
        console.error("Failed to load notifications:", error);
    }
}

async function selectConversation(conversationId, conversationName = "Chat", conversationMeta = "", avatarUrl = "") {
    const id = Number(conversationId);
    if (!id) return;

    // Reset polling message state for switching
    selectedConversationId = id;
    selectedConversationName = conversationName;
    lastMessageId = null;
    lastRenderedSignature = "";

    try {
        sessionStorage.setItem("openchat-selected-conversation-id", String(id));
    } catch (e) {}

    // Ensure messages from previous conversation CANNOT remain visible
    if (chatBox) {
        chatBox.innerHTML = "";
    }

    // Update conversation header
    const titleNode = document.querySelector(".conversation-name");
    const metaNode = document.querySelector(".conversation-meta-row");
    const avatarNode = document.querySelector(".conversation-avatar");
    if (titleNode) {
        titleNode.textContent = conversationName;
    }
    if (metaNode) {
        if (id === 1) {
            metaNode.innerHTML = '<span class="online-dot"></span>Official Open.Chat space';
        } else {
            metaNode.innerHTML = `<span class="online-dot"></span>${escapeHTML(conversationMeta || "Direct message")}`;
        }
    }
    if (avatarNode) {
        if (id === 1) {
            avatarNode.className = "conversation-avatar enormous";
            avatarNode.textContent = "E";
        } else {
            avatarNode.className = "conversation-avatar";
            avatarNode.innerHTML = buildAvatarMarkup(avatarUrl, conversationName);
        }
    }

    if (messageInput) {
        messageInput.placeholder = `Message ${conversationName}`;
    }

    // Update active highlight on chat items
    document.querySelectorAll(".chat-item").forEach((chatItem) => {
        const itemId = Number(chatItem.dataset.conversationId);
        chatItem.classList.toggle("active", itemId === selectedConversationId);
    });

    await loadActiveConversationMessages(true);

    try {
        await fetch(`/api/conversations/${selectedConversationId}/read`, { method: "POST", cache: "no-store" });
    } catch (e) {}
    await refreshTypingState();
}

async function sendMessage(message, file) {
    if (!selectedConversationId) {
        console.error("No conversation selected");
        return;
    }

    const formData = new FormData();
    if (message) {
        formData.append("message", message);
    }
    if (file) {
        formData.append("attachment", file);
    }

    const response = await fetch(`/api/conversations/${selectedConversationId}/messages`, {
        method: "POST",
        body: formData,
        cache: "no-store",
    });

    if (!response.ok) {
        const errPayload = await response.json().catch(() => ({}));
        throw new Error(errPayload.error || "Message send failed");
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

        lastTypingState = false;
        await setTypingState(false);
        messageInput.value = "";
        if (attachmentInput) {
            attachmentInput.value = "";
        }
        messageInput.focus();
        try {
            await sendMessage(message, file);
            if (selectedConversationId && currentUserId) {
                await fetch(`/api/conversations/${selectedConversationId}/read`, { method: "POST", cache: "no-store" });
            }
        } catch (error) {
            console.error("Message send error:", error);
        }
    });
}

if (messageInput) {
    messageInput.addEventListener("input", () => {
        if (!selectedConversationId || !currentUserId) {
            return;
        }
        if (typingTimer) {
            clearTimeout(typingTimer);
        }
        setTypingState(true);
        typingTimer = setTimeout(() => setTypingState(false), 1500);
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

async function loadConversationList() {
    if (!currentUserId) {
        return;
    }

    const container = document.getElementById("conversation-items-container");
    const pinnedEnormous = document.getElementById("enormous-chat-item");

    try {
        const response = await fetch("/api/conversations", { cache: "no-store" });
        if (!response.ok) {
            return;
        }

        const conversations = await response.json();
        if (!Array.isArray(conversations)) {
            return;
        }

        // 1. Update pinned ENORMOUS item
        const enormousData = conversations.find(c => c.conversation_id === 1);
        if (enormousData && pinnedEnormous) {
            const timeSpan = pinnedEnormous.querySelector(".enormous-time");
            const snippet = pinnedEnormous.querySelector(".enormous-last-message");
            if (timeSpan && enormousData.last_message_at) {
                timeSpan.textContent = formatDeviceTimestamp(enormousData.last_message_at);
            }
            if (snippet && enormousData.last_message_text) {
                snippet.textContent = enormousData.last_message_text;
            }
            pinnedEnormous.classList.toggle("active", selectedConversationId === 1);
        }

        if (!container) {
            return;
        }

        // 2. Render user personal chats and groups
        const otherConversations = conversations.filter(c => c.conversation_id !== 1);
        container.innerHTML = "";

        if (otherConversations.length === 0) {
            container.innerHTML = '<div class="chat-list-empty"><small>No conversations yet. Start a new chat or group!</small></div>';
            return;
        }

        otherConversations.forEach((conversation) => {
            const item = document.createElement("button");
            item.type = "button";
            item.className = "chat-item";
            if (conversation.conversation_id === selectedConversationId) {
                item.classList.add("active");
            }
            item.dataset.conversationId = String(conversation.conversation_id);
            item.dataset.room = conversation.title || "Chat";

            const initials = (conversation.title || "Chat").split(" ").map((part) => part[0]).slice(0, 2).join("").toUpperCase() || "?";
            const lastText = conversation.last_message_text ? escapeHTML(conversation.last_message_text) : "Start a conversation";
            const timestamp = conversation.last_message_at ? formatDeviceTimestamp(conversation.last_message_at) : "Now";
            const unreadBadge = conversation.unread_count > 0 ? `<span class="badge">${conversation.unread_count}</span>` : "";

            item.innerHTML = `
                <div class="chat-avatar">${buildAvatarMarkup(conversation.avatar_url, conversation.title || "Chat")}</div>
                <div class="chat-copy">
                    <div class="chat-row">
                        <strong>${escapeHTML(conversation.title || "Chat")}</strong>
                        <span>${escapeHTML(timestamp)}</span>
                    </div>
                    <small>${lastText}</small>
                </div>
                ${unreadBadge}
            `;

            item.addEventListener("click", () => {
                const metaText = conversation.kind === "group" ? "Group chat" : "Direct message";
                selectConversation(conversation.conversation_id, conversation.title || "Chat", metaText, conversation.avatar_url || "");
            });

            container.appendChild(item);
        });
    } catch (error) {
        console.error("Failed to load conversations:", error);
    }
}

async function startPolling() {
    if (document.hidden) {
        return;
    }

    if (selectedConversationId) {
        await loadActiveConversationMessages(false);
    }
    if (currentUserId) {
        await loadConversationList();
        await loadNotifications();
        await refreshTypingState();
    }
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

const pinnedEnormousBtn = document.getElementById("enormous-chat-item");
if (pinnedEnormousBtn) {
    pinnedEnormousBtn.addEventListener("click", () => {
        selectConversation(1, pinnedEnormousBtn.dataset.room || "ENORMOUS", "Official Open.Chat space");
    });
}

// Initial load: activate selected conversation and begin polling
selectConversation(selectedConversationId, selectedConversationId === 1 ? "ENORMOUS" : "Chat");
startPolling();

