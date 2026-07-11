# test_env.py
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

print("🔄 .env ফাইল থেকে ডেটা লোড করার চেষ্টা করা হচ্ছে...")
load_dotenv()

db_url = os.getenv("DATABASE_URL")

if not db_url:
    print("❌ ব্যর্থ! .env ফাইল থেকে DATABASE_URL রিড করা যাচ্ছে না।")
    print("👉 নিশ্চিত করুন test_env.py এবং .env ফাইল একই ফোল্ডারে আছে।")
else:
    print("✅ সফল! .env ফাইল থেকে ইউআরএলটি রিড করা গেছে।")
    # সিকিউরিটির জন্য পাসওয়ার্ড মাস্ক করে প্রিন্ট করা
    masked_url = db_url.split("@")[-1] if "@" in db_url else db_url
    print(f"🔗 ডেটাবেজ হোস্ট: ...@{masked_url}")
    
    # SQLModel/SQLAlchemy-র সাথে মানানসই ফরম্যাট করা
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+psycopg2://", 1)
        
    print("\n⚡ এখন Neon DB-র সাথে কানেকশন টেস্ট করা হচ্ছে...")
    try:
        engine = create_engine(db_url)
        with engine.connect() as conn:
            result = conn.execute(text("SELECT version();")).fetchone()
            print("🚀 কানেকশন ১০০% সফল! Neon DB ভার্সন:")
            print(f"   {result[0]}")
    except Exception as e:
        print("❌ ডেটাবেজ কানেকশন ব্যর্থ হয়েছে!")
        print(f"📁 এরর মেসেজ: {e}")