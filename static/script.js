const randomID = Math.floor(Math.random() * 9000) + 1000;
const username = "Anonymous " + randomID;
const socket = io({
    transports: ["polling"]
});

const messageInput = document.getElementById("message-input");
const sendButton = document.getElementById("send-button");
const chatBox = document.getElementById("chat-box");
const onlineUsers = document.getElementById("online-users");
sendButton.addEventListener("click", function () {

    const message = messageInput.value;

    if (message.trim() === "") {
        return;
    }

    socket.send({
    username: username,
    message: message
});

    messageInput.value = "";
});
messageInput.addEventListener("keypress", function(event) {

    if (event.key === "Enter") {

        event.preventDefault();

        sendButton.click();

    }

});
socket.on("message", function(data) {

    chatBox.innerHTML += `
        <div class="message">
            <div class="message-header">
                <b>${data.username}</b>
                <span>${data.time}</span>
            </div>

            <div class="message-body">
                ${data.message}
            </div>
        </div>
    `;

    chatBox.scrollTop = chatBox.scrollHeight;

});socket.on("online_users", function(count) {

    if (count === 1) {
        onlineUsers.innerHTML = "🟢 1 User Online";
    } else {
        onlineUsers.innerHTML = "🟢 " + count + " Users Online";
    }

});
