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

const username = document.body.dataset.currentUsername || storedUser || `Anonymous ${randomID}`;
const currentUserId = Number(document.body.dataset.currentUserId || "0") || null;
let configuredCallIceServers = [];
try {
    configuredCallIceServers = JSON.parse(document.body.dataset.callIceServers || "[]");
} catch (error) {
    console.error("Invalid call ICE server configuration:", error);
}

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
let selectedOtherUserId = null;
let conversationCache = [];
let activeCall = null;
let callPollInProgress = false;
let callPollTimer = null;

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
                            await selectConversation(payload.conversation_id, button.closest(".contact-result")?.querySelector("strong")?.textContent || "Direct chat", "Direct message", button.dataset.avatarUrl || "", userId);

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
            <p>${selectedOtherUserId ? `Call ${escapeHTML(selectedConversationName)} in this direct chat.` : "Open a direct chat to place a call."}</p>
            <div class="modal-actions">
                <button type="button" class="modal-action-btn" data-start-call-mode="audio" ${selectedOtherUserId ? "" : "disabled"}>Voice call</button>
                <button type="button" class="modal-action-btn" data-start-call-mode="video" ${selectedOtherUserId ? "" : "disabled"}>Video call</button>
                <button type="button" class="secondary-modal-btn" data-close-modal="true">Close</button>
            </div>
        `;
        body.querySelectorAll("[data-start-call-mode]").forEach((button) => {
            button.addEventListener("click", () => {
                modal.classList.remove("visible");
                startOutgoingCall(button.dataset.startCallMode);
            });
        });
    }

    modal.classList.add("visible");
}

function ensureCallOverlay() {
    let overlay = document.getElementById("openchat-call-overlay");
    if (overlay) return overlay;

    overlay = document.createElement("div");
    overlay.id = "openchat-call-overlay";
    overlay.className = "call-overlay";
    overlay.innerHTML = `
        <section class="call-panel" role="dialog" aria-modal="true" aria-labelledby="call-title">
            <header class="call-panel-header">
                <div><h2 id="call-title">Call</h2><p id="call-status-text">Connecting</p></div>
                <button type="button" class="call-end-button" data-call-end aria-label="End call" title="End call">End</button>
            </header>
            <div class="call-media" id="call-media">
                <video id="call-remote-video" autoplay playsinline></video>
                <video id="call-local-video" autoplay playsinline muted></video>
                <audio id="call-remote-audio" autoplay></audio>
            </div>
            <div class="call-incoming-actions" id="call-incoming-actions"></div>
            <div class="call-controls" id="call-controls">
                <button type="button" data-call-mute aria-pressed="false">Mute</button>
                <button type="button" data-call-camera aria-pressed="false">Camera off</button>
            </div>
        </section>
    `;
    document.body.appendChild(overlay);
    overlay.querySelector("[data-call-end]").addEventListener("click", () => finishCall("ended", true));
    overlay.querySelector("[data-call-mute]").addEventListener("click", toggleCallMute);
    overlay.querySelector("[data-call-camera]").addEventListener("click", toggleCallCamera);
    return overlay;
}

function setCallStatusText(text) {
    const status = document.getElementById("call-status-text");
    if (status) status.textContent = text;
}

function armCallRingingTimeout(call) {
    clearTimeout(call.ringTimer);
    if (call.status !== "ringing") return;
    call.ringTimer = setTimeout(() => {
        if (activeCall?.id === call.id && activeCall.status === "ringing") finishCall("missed", true);
    }, 60000);
}

async function callApi(path, options = {}) {
    const response = await fetch(path, {
        cache: "no-store",
        ...options,
        headers: { ...(options.body ? { "Content-Type": "application/json" } : {}), ...(options.headers || {}) },
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || `Call request failed (${response.status})`);
    return payload;
}

function showCallOverlay(call, incoming = false) {
    const overlay = ensureCallOverlay();
    armCallRingingTimeout(call);
    const videoMode = call.mode === "video";
    overlay.classList.add("visible");
    overlay.classList.toggle("audio-only", !videoMode);
    overlay.querySelector("#call-title").textContent = call.caller_username
        ? (incoming ? `${call.caller_username} is calling` : `Call with ${call.caller_username}`)
        : `${videoMode ? "Video" : "Voice"} call with ${selectedConversationName}`;
    const actions = overlay.querySelector("#call-incoming-actions");
    const controls = overlay.querySelector("#call-controls");
    actions.innerHTML = incoming
        ? '<button type="button" data-call-answer>Answer</button><button type="button" data-call-decline>Decline</button>'
        : "";
    controls.hidden = incoming;
    overlay.querySelector("[data-call-end]").hidden = incoming;
    if (incoming) {
        actions.querySelector("[data-call-answer]").addEventListener("click", () => acceptIncomingCall());
        actions.querySelector("[data-call-decline]").addEventListener("click", () => finishCall("rejected", true));
        setCallStatusText(`${videoMode ? "Video" : "Voice"} call incoming`);
    } else {
        setCallStatusText(call.status === "ringing" ? "Calling…" : "Connecting…");
    }
    const cameraButton = overlay.querySelector("[data-call-camera]");
    cameraButton.hidden = !videoMode;
}

async function postCallSignal(signalType, payload) {
    if (!activeCall) return;
    await callApi(`/api/calls/${activeCall.id}/signals`, {
        method: "POST",
        body: JSON.stringify({ signal_type: signalType, payload }),
    });
}

async function createCallPeerConnection() {
    const overlay = ensureCallOverlay();
    const peer = new RTCPeerConnection({
        iceServers: [{ urls: "stun:stun.l.google.com:19302" }, ...configuredCallIceServers],
        iceCandidatePoolSize: 4,
        bundlePolicy: "max-bundle",
    });
    activeCall.peer = peer;
    activeCall.localStream.getTracks().forEach((track) => peer.addTrack(track, activeCall.localStream));
    peer.onicecandidate = (event) => {
        if (event.candidate) {
            postCallSignal("ice-candidate", event.candidate.toJSON()).catch((error) => console.error("Call ICE signaling failed:", error));
        }
    };
    peer.onconnectionstatechange = () => {
        console.debug("Call peer connection state:", peer.connectionState);
        if (!activeCall || activeCall.peer !== peer) return;
        if (peer.connectionState === "connected") {
            clearTimeout(activeCall.disconnectTimer);
            activeCall.disconnectTimer = null;
            setCallStatusText("Connected");
        } else if (peer.connectionState === "disconnected") {
            setCallStatusText("Reconnecting…");
            clearTimeout(activeCall.disconnectTimer);
            activeCall.disconnectTimer = setTimeout(() => {
                if (activeCall?.peer === peer && peer.connectionState === "disconnected") finishCall("ended", true);
            }, 15000);
        } else if (peer.connectionState === "failed") {
            if (activeCall.iceRestarted) {
                finishCall("ended", true);
                return;
            }
            activeCall.iceRestarted = true;
            setCallStatusText("Reconnecting…");
            peer.restartIce();
            peer.createOffer({ iceRestart: true })
                .then((offer) => peer.setLocalDescription(offer))
                .then(() => postCallSignal("offer", peer.localDescription.toJSON()))
                .catch((error) => {
                    console.error("Call ICE restart failed:", error);
                    if (activeCall?.peer === peer) finishCall("ended", true);
                });
        } else if (peer.connectionState === "closed") {
            finishCall("ended", true);
        }
    };
    peer.ontrack = (event) => {
        if (!activeCall || activeCall.peer !== peer) return;
        const stream = event.streams[0] || new MediaStream([event.track]);
        const remoteVideo = overlay.querySelector("#call-remote-video");
        remoteVideo.srcObject = stream;
        remoteVideo.muted = true;
        overlay.querySelector("#call-remote-audio").srcObject = stream;
        overlay.querySelector("#call-local-video").srcObject = activeCall.localStream;
        overlay.querySelector("#call-remote-video").play().catch((error) => console.debug("Remote video autoplay deferred:", error));
        overlay.querySelector("#call-remote-audio").play().catch((error) => console.debug("Remote audio autoplay deferred:", error));
    };
    overlay.querySelector("#call-local-video").srcObject = activeCall.localStream;
}

async function startOutgoingCall(mode) {
    if (!currentUserId || !selectedOtherUserId || !["audio", "video"].includes(mode)) {
        showActionModal("calls");
        return;
    }
    if (activeCall) {
        setCallStatusText("Finish the current call before starting another.");
        return;
    }
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia || !window.RTCPeerConnection) {
        const error = new Error("Calls require a supported browser and a secure connection (HTTPS).");
        showCallOverlay({ mode, status: "failed" });
        document.querySelector("#call-title").textContent = "Call unavailable";
        setCallStatusText(error.message);
        setTimeout(() => document.getElementById("openchat-call-overlay")?.classList.remove("visible"), 5000);
        return;
    }

    let localStream;
    let call;
    try {
        call = await callApi("/api/calls/start", {
            method: "POST",
            body: JSON.stringify({ target_user_id: selectedOtherUserId, conversation_id: selectedConversationId, mode }),
        });
        activeCall = { ...call, localStream: null, lastSignalId: 0, pendingIce: [], iceRestarted: false };
        showCallOverlay(activeCall);
        localStream = await navigator.mediaDevices.getUserMedia({
            audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
            video: mode === "video" ? { width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 24, max: 30 } } : false,
        });
        if (activeCall?.id !== call.id) {
            localStream.getTracks().forEach((track) => track.stop());
            return;
        }
        activeCall.localStream = localStream;
        await createCallPeerConnection();
        const offer = await activeCall.peer.createOffer();
        await activeCall.peer.setLocalDescription(offer);
        await postCallSignal("offer", activeCall.peer.localDescription.toJSON());
        console.debug("Started WebRTC call:", { callId: call.id, mode, targetUserId: selectedOtherUserId });
    } catch (error) {
        const failedCall = activeCall?.id === call?.id ? activeCall : null;
        if (failedCall) activeCall = null;
        localStream?.getTracks().forEach((track) => track.stop());
        if (failedCall) {
            callApi(`/api/calls/${failedCall.id}/status`, { method: "POST", body: JSON.stringify({ status: "cancelled" }) }).catch(() => {});
        }
        console.error("Unable to start call:", error);
        const overlay = ensureCallOverlay();
        overlay.classList.add("visible");
        overlay.querySelector("#call-title").textContent = "Call unavailable";
        setCallStatusText(error.message || "Unable to start call");
        overlay.querySelector("[data-call-end]").hidden = false;
        setTimeout(() => document.getElementById("openchat-call-overlay")?.classList.remove("visible"), 3000);
    }
}

async function acceptIncomingCall() {
    if (!activeCall) return;
    const callId = activeCall.id;
    try {
        if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
            throw new Error("Microphone access requires a supported browser and a secure connection (HTTPS).");
        }
        const localStream = await navigator.mediaDevices.getUserMedia({
            audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
            video: activeCall.mode === "video" ? { width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 24, max: 30 } } : false,
        });
        if (activeCall?.id !== callId) {
            localStream.getTracks().forEach((track) => track.stop());
            return;
        }
        activeCall.localStream = localStream;
        await callApi(`/api/calls/${activeCall.id}/status`, { method: "POST", body: JSON.stringify({ status: "accepted" }) });
        activeCall.status = "accepted";
        clearTimeout(activeCall.ringTimer);
        showCallOverlay(activeCall);
        await createCallPeerConnection();
        setCallStatusText("Connecting…");
        await pollCallSignals();
    } catch (error) {
        console.error("Unable to answer call:", error);
        await finishCall("rejected", true);
        const overlay = ensureCallOverlay();
        overlay.classList.add("visible");
        overlay.querySelector("#call-title").textContent = "Could not join call";
        overlay.querySelector("#call-incoming-actions").innerHTML = "";
        overlay.querySelector("#call-controls").hidden = true;
        overlay.querySelector("[data-call-end]").hidden = true;
        setCallStatusText(error.message || "Unable to answer call");
        setTimeout(() => overlay.classList.remove("visible"), 5000);
    }
}

async function handleCallSignal(signal) {
    if (!activeCall?.peer) return;
    const peer = activeCall.peer;
    if (signal.signal_type === "offer") {
        await peer.setRemoteDescription(new RTCSessionDescription(signal.payload));
        const answer = await peer.createAnswer();
        await peer.setLocalDescription(answer);
        await postCallSignal("answer", peer.localDescription.toJSON());
        setCallStatusText("Connecting…");
    } else if (signal.signal_type === "answer") {
        await peer.setRemoteDescription(new RTCSessionDescription(signal.payload));
        setCallStatusText("Connecting…");
    } else if (signal.signal_type === "ice-candidate") {
        const candidate = new RTCIceCandidate(signal.payload);
        if (peer.remoteDescription) await peer.addIceCandidate(candidate);
        else activeCall.pendingIce.push(candidate);
    }
    if (peer.remoteDescription && activeCall.pendingIce.length) {
        const candidates = activeCall.pendingIce.splice(0);
        for (const candidate of candidates) await peer.addIceCandidate(candidate);
    }
}

async function pollCallSignals() {
    if (!activeCall?.peer) return;
    const signals = await callApi(`/api/calls/${activeCall.id}/signals?after_id=${activeCall.lastSignalId}`);
    for (const signal of signals) {
        activeCall.lastSignalId = signal.id;
        await handleCallSignal(signal);
    }
}

async function finishCall(status, notifyPeer) {
    if (!activeCall) return;
    const call = activeCall;
    activeCall = null;
    clearTimeout(call.ringTimer);
    if (notifyPeer) {
        try {
            await callApi(`/api/calls/${call.id}/status`, { method: "POST", body: JSON.stringify({ status }) });
        } catch (error) {
            console.debug("Call status update failed:", error);
        }
    }
    clearTimeout(call.disconnectTimer);
    call.peer?.close();
    call.localStream?.getTracks().forEach((track) => track.stop());
    const overlay = document.getElementById("openchat-call-overlay");
    if (overlay) {
        overlay.classList.remove("visible");
        overlay.querySelector("#call-remote-video").srcObject = null;
        overlay.querySelector("#call-local-video").srcObject = null;
        overlay.querySelector("#call-remote-audio").srcObject = null;
    }
    console.debug("Call finished:", { callId: call.id, status });
}

function toggleCallMute(event) {
    if (!activeCall?.localStream) return;
    const tracks = activeCall.localStream.getAudioTracks();
    const enabled = tracks.some((track) => track.enabled);
    tracks.forEach((track) => { track.enabled = !enabled; });
    event.currentTarget.textContent = enabled ? "Unmute" : "Mute";
    event.currentTarget.setAttribute("aria-pressed", enabled ? "true" : "false");
}

function toggleCallCamera(event) {
    if (!activeCall?.localStream) return;
    const tracks = activeCall.localStream.getVideoTracks();
    const enabled = tracks.some((track) => track.enabled);
    tracks.forEach((track) => { track.enabled = !enabled; });
    event.currentTarget.textContent = enabled ? "Camera on" : "Camera off";
    event.currentTarget.setAttribute("aria-pressed", enabled ? "true" : "false");
}

async function pollCallState() {
    if (!currentUserId) return;
    if (callPollInProgress) {
        scheduleCallStatePoll(250);
        return;
    }
    callPollInProgress = true;
    try {
        if (activeCall) {
            const call = await callApi(`/api/calls/${activeCall.id}`);
            if (!["ringing", "accepted"].includes(call.status)) {
                await finishCall(call.status, false);
            } else {
                activeCall.status = call.status;
                if (call.status !== "ringing") clearTimeout(activeCall.ringTimer);
                if (call.status === "accepted" && activeCall.peer) {
                    setCallStatusText("Connecting…");
                    await pollCallSignals();
                }
            }
        } else {
            const calls = await callApi("/api/calls");
            const incoming = calls.find((call) => call.target_id === currentUserId && call.status === "ringing");
            if (incoming) {
                activeCall = { ...incoming, lastSignalId: 0, pendingIce: [] };
                showCallOverlay(activeCall, true);
                console.debug("Incoming WebRTC call:", incoming);
            }
        }
    } catch (error) {
        console.debug("Call polling failed:", error);
    } finally {
        callPollInProgress = false;
        scheduleCallStatePoll(activeCall ? 700 : 2000);
    }
}

function scheduleCallStatePoll(delay) {
    if (callPollTimer) clearTimeout(callPollTimer);
    callPollTimer = setTimeout(() => {
        callPollTimer = null;
        pollCallState();
    }, delay);
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

document.querySelectorAll("[data-call-mode]").forEach((button) => {
    button.addEventListener("click", () => startOutgoingCall(button.dataset.callMode));
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
let lastConversationSignature = "";
let lastMessageId = null;
let pollingTimer = null;
let pollingInProgress = false;
let nextConversationRefreshAt = 0;
let nextNotificationRefreshAt = 0;
let nextTypingRefreshAt = 0;
let nextPresenceRefreshAt = 0;

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
    lastMessageId = messages && messages.length
        ? Math.max(...messages.map((message) => Number(message.id) || 0))
        : 0;
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
        if (!titleNode) {
            return;
        }
        const metaNode = document.querySelector(".conversation-meta-row");
        if (!typingUsers.length) {
            titleNode.removeAttribute("data-typing-text");
            if (metaNode && selectedConversationId !== 1) {
                const selected = conversationCache.find((item) => Number(item.conversation_id) === selectedConversationId);
                const label = selected?.kind === "group" ? "Group chat" : "Direct message";
                metaNode.innerHTML = `<span class="online-dot"></span>${label}`;
            }
            return;
        }
        const userName = typingUsers[0].user_id ? `User ${typingUsers[0].user_id}` : "Someone";
        titleNode.setAttribute("data-typing-text", `${userName} is typing...`);
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

async function selectConversation(conversationId, conversationName = "Chat", conversationMeta = "", avatarUrl = "", otherUserId = null) {
    const id = Number(conversationId);
    if (!id) return;

    // Reset polling message state for switching
    selectedConversationId = id;
    selectedConversationName = conversationName;
    selectedOtherUserId = Number(otherUserId) || conversationCache.find((item) => Number(item.conversation_id) === id)?.other_user_id || null;
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
        conversationCache = conversations;
        const selectedConversation = conversations.find((item) => Number(item.conversation_id) === selectedConversationId);
        selectedOtherUserId = selectedConversation?.other_user_id || null;
        const signature = JSON.stringify(conversations);
        if (signature === lastConversationSignature) {
            return;
        }
        lastConversationSignature = signature;

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
                selectConversation(conversation.conversation_id, conversation.title || "Chat", metaText, conversation.avatar_url || "", conversation.other_user_id);
            });

            container.appendChild(item);
        });
    } catch (error) {
        console.error("Failed to load conversations:", error);
    }
}

async function startPolling() {
    if (pollingInProgress) return;
    pollingInProgress = true;
    pollingTimer = null;
    if (document.hidden) {
        pollingInProgress = false;
        return;
    }

    try {
        const now = Date.now();
        const updates = [];
        if (selectedConversationId) updates.push(loadActiveConversationMessages(false));
        if (currentUserId) {
            if (now >= nextConversationRefreshAt) {
                nextConversationRefreshAt = now + 10000;
                updates.push(loadConversationList());
            }
            if (now >= nextNotificationRefreshAt) {
                nextNotificationRefreshAt = now + 20000;
                updates.push(loadNotifications());
            }
            if (now >= nextTypingRefreshAt) {
                nextTypingRefreshAt = now + 2500;
                updates.push(refreshTypingState());
            }
            if (now >= nextPresenceRefreshAt) {
                nextPresenceRefreshAt = now + 20000;
                updates.push(updatePresence());
            }
        }
        await Promise.allSettled(updates);
    } finally {
        pollingInProgress = false;
        if (!document.hidden) pollingTimer = setTimeout(startPolling, 1800);
    }
}


if (typeof document !== "undefined") {
    scheduleCallStatePoll(1000);
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

