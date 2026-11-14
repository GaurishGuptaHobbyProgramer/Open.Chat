function addCell(row, value) {
    const cell = document.createElement("td");
    cell.textContent = value === null || value === undefined || value === "" ? "-" : String(value);
    row.appendChild(cell);
    return cell;
}

function addEmptyRow(body, colspan, label) {
    const row = document.createElement("tr");
    const cell = addCell(row, label);
    cell.colSpan = colspan;
    body.replaceChildren(row);
}

async function getDeveloperData(path) {
    const response = await fetch(path, { cache: "no-store", headers: { Accept: "application/json" } });
    const data = await response.json();
    if (!response.ok) {
        throw new Error(data.error || `Request failed (${response.status})`);
    }
    return data;
}

function createAccountActionButton(account) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `developer-action-button${account.is_suspended ? " is-suspended" : ""}`;
    button.textContent = account.is_suspended ? "Reactivate" : "Suspend";
    button.disabled = account.is_developer;
    if (account.is_developer) {
        button.title = "Developer accounts are protected from suspension.";
    } else {
        button.addEventListener("click", () => updateAccountSuspension(account, button));
    }
    return button;
}

async function lookupAccount(userId) {
    const result = document.getElementById("account-id-result");
    result.textContent = "Looking up account...";
    result.classList.remove("is-error");
    try {
        const account = await getDeveloperData(`/api/developer/users/${userId}`);
        const summary = document.createElement("span");
        summary.className = "developer-id-result-summary";
        summary.textContent = `#${account.id} ${account.username} · ${account.account_status}${account.is_developer ? " · developer account" : ""}`;
        result.replaceChildren(summary, createAccountActionButton(account));
    } catch (error) {
        result.textContent = error.message || `Could not look up account #${userId}.`;
        result.classList.add("is-error");
    }
}

function renderAccounts(accounts) {
    const body = document.getElementById("accounts-rows");
    body.replaceChildren();
    document.getElementById("account-count").textContent = `${accounts.length} shown`;
    if (!accounts.length) {
        addEmptyRow(body, 9, "No accounts found.");
        return;
    }
    accounts.forEach((account) => {
        const row = document.createElement("tr");
        addCell(row, `#${account.id}`).classList.add("developer-id-cell");
        addCell(row, account.username);
        addCell(row, account.display_name);
        addCell(row, account.created_at);
        addCell(row, account.profile_status);
        addCell(row, account.account_status);
        addCell(row, account.is_developer ? "Yes" : "No");
        addCell(row, `${account.online_status} / ${account.last_activity}`);
        const actionCell = document.createElement("td");
        actionCell.appendChild(createAccountActionButton(account));
        row.appendChild(actionCell);
        body.appendChild(row);
    });
}

async function updateAccountSuspension(account, button) {
    const suspended = !account.is_suspended;
    const action = suspended ? "suspend" : "reactivate";
    if (suspended && !window.confirm(`Suspend account #${account.id} (${account.username})? They will be signed out on their next request.`)) {
        return;
    }

    const status = document.getElementById("account-action-status");
    button.disabled = true;
    status.textContent = `${action === "suspend" ? "Suspending" : "Reactivating"} account #${account.id}...`;
    status.classList.remove("is-error");

    try {
        const response = await fetch(`/api/developer/users/${account.id}/suspension`, {
            method: "POST",
            cache: "no-store",
            headers: { Accept: "application/json", "Content-Type": "application/json" },
            body: JSON.stringify({ suspended }),
        });
        const result = await response.json();
        if (!response.ok) {
            throw new Error(result.error || `Request failed (${response.status})`);
        }
        status.textContent = `Account #${account.id} ${suspended ? "suspended" : "reactivated"}.`;
        const accounts = await getDeveloperData("/api/developer/users");
        renderAccounts(accounts);
        await lookupAccount(account.id);
    } catch (error) {
        status.textContent = error.message || `Could not ${action} account #${account.id}.`;
        status.classList.add("is-error");
        button.disabled = false;
    }
}

function renderConversations(conversations) {
    const body = document.getElementById("conversation-rows");
    body.replaceChildren();
    document.getElementById("conversation-count").textContent = `${conversations.length} shown`;
    if (!conversations.length) {
        addEmptyRow(body, 7, "No conversations found.");
        return;
    }
    conversations.forEach((conversation) => {
        const row = document.createElement("tr");
        addCell(row, conversation.id);
        addCell(row, conversation.kind);
        addCell(row, conversation.title);
        addCell(row, conversation.members.map((member) => member.display_name).join(", "));
        addCell(row, conversation.message_count);
        addCell(row, conversation.last_message_at || conversation.created_at);
        const actionCell = document.createElement("td");
        const button = document.createElement("button");
        button.type = "button";
        button.className = "developer-inspect-button";
        button.textContent = "Inspect";
        button.addEventListener("click", () => loadMessages(conversation));
        actionCell.appendChild(button);
        row.appendChild(actionCell);
        body.appendChild(row);
    });
}

async function loadMessages(conversation) {
    const body = document.getElementById("message-rows");
    document.getElementById("selected-conversation").textContent = `${conversation.title} · ${conversation.kind} · ID ${conversation.id}`;
    addEmptyRow(body, 3, "Loading messages...");
    try {
        const data = await getDeveloperData(`/api/developer/conversations/${conversation.id}/messages?limit=200`);
        body.replaceChildren();
        if (!data.messages.length) {
            addEmptyRow(body, 3, "No messages in this conversation.");
        } else {
            data.messages.forEach((message) => {
                const row = document.createElement("tr");
                addCell(row, message.created_at);
                const recipients = message.recipients.length ? message.recipients.join(", ") : "-";
                addCell(row, `${message.sender} → ${recipients}`);
                addCell(row, message.text || (message.attachment_name ? `[Attachment: ${message.attachment_name}]` : "[Empty message]"));
                body.appendChild(row);
            });
        }
        document.getElementById("message-limit-note").textContent = `Showing the latest ${data.messages.length} messages (maximum 200).`;
    } catch (error) {
        addEmptyRow(body, 3, error.message || "Could not load messages.");
    }
}

function renderEvents(events) {
    const body = document.getElementById("event-rows");
    body.replaceChildren();
    document.getElementById("event-count").textContent = `${events.length} shown`;
    if (!events.length) {
        addEmptyRow(body, 6, "No recorded events yet.");
        return;
    }
    events.forEach((event) => {
        const row = document.createElement("tr");
        addCell(row, event.created_at);
        addCell(row, event.event_type);
        addCell(row, event.endpoint);
        addCell(row, event.response_status);
        addCell(row, event.user_id);
        addCell(row, event.detail);
        body.appendChild(row);
    });
}

async function loadDeveloperCenter() {
    try {
        const [system, accounts, conversations, events] = await Promise.all([
            getDeveloperData("/api/developer/system"),
            getDeveloperData("/api/developer/users"),
            getDeveloperData("/api/developer/conversations"),
            getDeveloperData("/api/developer/events?limit=100"),
        ]);
        document.getElementById("system-health").textContent = `Database ${system.database_health}`;
        const summary = document.getElementById("system-summary");
        summary.textContent = `Open.Chat AI: ${system.ai_enabled ? "enabled by server configuration" : "Under Development"} · Debug mode: ${system.debug_mode ? "on" : "off"} · Presence sessions: ${system.presence_sessions} · Account mapping: ${system.presence_account_mapping} · ${system.persistent_errors}`;
        const counts = document.getElementById("table-counts");
        counts.replaceChildren();
        Object.entries(system.table_counts).sort(([left], [right]) => left.localeCompare(right)).forEach(([name, count]) => {
            const item = document.createElement("span");
            item.textContent = `${name}: ${count}`;
            counts.appendChild(item);
        });
        renderAccounts(accounts);
        renderConversations(conversations);
        renderEvents(events);
    } catch (error) {
        document.getElementById("system-health").textContent = "Unavailable";
        document.getElementById("system-summary").textContent = error.message || "Could not load control center data.";
    }
}

document.getElementById("account-id-lookup").addEventListener("submit", (event) => {
    event.preventDefault();
    const userId = Number.parseInt(document.getElementById("account-id-input").value, 10);
    if (Number.isSafeInteger(userId) && userId > 0) {
        lookupAccount(userId);
    }
});

loadDeveloperCenter();
