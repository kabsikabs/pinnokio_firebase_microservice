import os

if __name__ == "__main__":
    # Valeurs par défaut
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8090"))
    # Démarre uvicorn
    import uvicorn

    uvicorn.run("app.main:app", host=host, port=port, reload=False)
