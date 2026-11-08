from app import create_app

app = create_app()

if __name__ == "__main__":
    # El modo debug solo se activa con FLASK_DEBUG=true y nunca con APP_ENV=production.
    app.run(debug=app.config["DEBUG"])
