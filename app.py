from flask import Flask, render_template, request
from flask_socketio import SocketIO, send, emit
from datetime import datetime
app = Flask(__name__)
app.config["SECRET_KEY"] = "openchat"

socketio = SocketIO(
    app,
    logger=True,
    engineio_logger=True,
    cors_allowed_origins="*"
)
connected_users = set()
@app.route("/")
def home():
    return render_template("index.html")

@socketio.on("message")
def handle_message(data):
    current_time = datetime.now().strftime("%I:%M %p")

    send({
        "username": data["username"],
        "message": data["message"],
        "time": current_time
    }, broadcast=True)
    
   
@socketio.on("connect")
def user_connected():
    connected_users.add(request.sid)

    emit(
        "online_users",
        len(connected_users),
        broadcast=True
    )





@socketio.on("disconnect")
def user_disconnected():
    connected_users.discard(request.sid)

    emit(
        "online_users",
        len(connected_users),
        broadcast=True
    )

    import os

if __name__ == "__main__":
    socketio.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000))
    )