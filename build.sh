#!/bin/bash
set -e

# 프론트엔드 빌드
cd frontend
npm install
npm run build
cd ..

# 빌드 결과를 백엔드 static 폴더로 복사
rm -rf backend/static
cp -r frontend/build backend/static

# 백엔드 의존성 설치
cd backend
pip install -r requirements.txt
