"""
DFS Edge Pro - Backend API
The path to $150-300M starts here!
"""

import sys
from pathlib import Path

# Add parent directory to path
parent_dir = str(Path(__file__).parent.parent)
sys.path.insert(0, parent_dir)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Import database and routes
from database.db import init_db, test_connection
from routers import auth, projections

app = FastAPI(
    title="DFS Edge Pro API",
    description="Professional DFS Platform - Building to $150M+",
    version="1.0.0"
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(auth.router)
app.include_router(projections.router)
# Startup event
@app.on_event("startup")
async def startup_event():
    """Initialize database on startup"""
    print("\n" + "="*60)
    print("🚀 DFS EDGE PRO - STARTING BACKEND API")
    print("="*60)
    print("📊 Vision: $150-300M exit in 5 years")
    print("🎯 Phase 1: Validate (1,000 users, $29K MRR)")
    print("💰 Mission: 10% to JSMS Academy")
    print("="*60)
    
    # Test database connection
    if test_connection():
        # Initialize database (create tables)
        init_db()
        print("✅ Authentication system ready!")
    else:
        print("⚠️  Database connection failed - check your .env file")
    
    print("="*60 + "\n")

@app.get("/")
async def root():
    """Welcome to DFS Edge Pro"""
    return {
        "message": "🚀 DFS Edge Pro API - Day 2 Complete!",
        "version": "1.0.0",
        "status": "Authentication Ready! 🔐",
        "new_features": {
            "signup": "POST /auth/signup",
            "login": "POST /auth/login",
            "get_user": "GET /auth/me"
        },
        "phase": "Phase 1: Validate",
        "progress": "Day 2: Users can now sign up and log in!",
        "next": "Day 3: Wrap your projection code",
        "docs": "/docs"
    }

@app.get("/health")
async def health():
    """Health check"""
    return {
        "status": "healthy",
        "day": "Day 2 - Authentication ✅",
        "next": "Day 3 - Projections API"
    }

@app.get("/api/vision")
async def vision():
    """The 5-year vision"""
    return {
        "vision": "Become #1 DFS platform globally",
        "current_milestone": "Day 2: Users can create accounts! 🎉",
        "milestones": {
            "year_1": "1,000 users, $348K revenue",
            "year_2": "5,000 users, $1.74M revenue",
            "year_3": "15,000 users, $5.22M revenue",
            "year_4": "30,000 users, $10.44M revenue",
            "year_5": "Exit for $150-300M"
        },
        "education_impact": "$2.1M+ to JSMS Academy over 5 years"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)