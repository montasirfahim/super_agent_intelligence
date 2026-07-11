# app/init_db.py
from app.database import engine
from sqlmodel import SQLModel


if __name__ == "__main__":
    print("Connecting to Neon DB and creating tables...")
    
   
    SQLModel.metadata.create_all(bind=engine)
    
    print("All tables created successfully in Neon DB! 🚀")