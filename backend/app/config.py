"""全局配置：全部参数可通过环境变量覆盖，容器内默认指向本地 PostgreSQL。"""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg://ccdev:ccdev@127.0.0.1:5432/ccdev",
    )
    jwt_secret: str = os.getenv("JWT_SECRET", "dev-only-secret-change-me-0123456789abcdef")
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = int(os.getenv("JWT_EXPIRE_MINUTES", "720"))
    # 方程残差 / 权益合计 / 股份类别合计的默认容差
    tolerance: float = float(os.getenv("EQUATION_TOLERANCE", "1e-8"))
    # 奇异值相对阈值：最小奇异值 / 最大奇异值 低于该值即判定不可唯一求解
    singular_threshold: float = float(os.getenv("SINGULAR_THRESHOLD", "1e-10"))
    # 条件数告警阈值（超过则判定数值不稳定，停止正式出数）
    condition_limit: float = float(os.getenv("CONDITION_LIMIT", "1e10"))
    cors_origins: str = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")


settings = Settings()
