import os

from .server import app

# Debug mode exposes the Werkzeug remote-code-execution debugger; opt in
# explicitly for local development only.
app.run(debug=os.getenv('FLASK_DEBUG') == '1', host="0.0.0.0")
