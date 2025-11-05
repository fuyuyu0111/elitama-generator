"""
Discord Webhookを使用した通知機能
"""

import os
import json
import requests
from typing import Optional, Dict, Any
from datetime import datetime


class DiscordNotifier:
    """Discord Webhook通知クラス"""
    
    def __init__(self, webhook_url: Optional[str] = None):
        """
        Args:
            webhook_url: Discord Webhook URL（Noneの場合は環境変数DISCORD_WEBHOOK_URLを使用）
        """
        self.webhook_url = webhook_url or os.environ.get('DISCORD_WEBHOOK_URL')
        if not self.webhook_url:
            raise ValueError("Discord Webhook URLが設定されていません。環境変数DISCORD_WEBHOOK_URLを設定するか、引数で指定してください。")
    
    def send_message(
        self,
        content: str,
        title: Optional[str] = None,
        color: Optional[int] = None,
        fields: Optional[list] = None,
        timestamp: bool = True
    ) -> bool:
        """
        Discordにメッセージを送信
        
        Args:
            content: メッセージ本文
            title: 埋め込みタイトル
            color: 埋め込みの色（0xRRGGBB形式の整数）
            fields: 追加フィールドのリスト [{"name": "...", "value": "...", "inline": True/False}]
            timestamp: タイムスタンプを追加するか
        
        Returns:
            成功した場合True
        """
        embed = {
            "description": content
        }
        
        if title:
            embed["title"] = title
        
        if color is not None:
            embed["color"] = color
        
        if fields:
            embed["fields"] = fields
        
        if timestamp:
            embed["timestamp"] = datetime.utcnow().isoformat()
        
        payload = {
            "embeds": [embed]
        }
        
        try:
            response = requests.post(
                self.webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"}
            )
            response.raise_for_status()
            return True
        except requests.exceptions.RequestException as e:
            print(f"Discord通知送信エラー: {e}")
            return False
    
    def send_success(self, message: str, details: Optional[Dict[str, Any]] = None) -> bool:
        """成功メッセージを送信（緑色）"""
        fields = []
        if details:
            for key, value in details.items():
                fields.append({
                    "name": key,
                    "value": str(value),
                    "inline": True
                })
        
        return self.send_message(
            content=message,
            title="✅ 処理成功",
            color=0x00ff00,  # 緑
            fields=fields if fields else None
        )
    
    def send_error(self, message: str, error: Optional[Exception] = None) -> bool:
        """エラーメッセージを送信（赤色）"""
        content = message
        if error:
            error_str = str(error)
            # Discordの文字数制限（2000文字）を考慮
            if len(error_str) > 1000:
                error_str = error_str[:1000] + "...（切り詰め）"
            content += f"\n\n**エラー詳細:**\n```\n{error_str}\n```"
        
        return self.send_message(
            content=content,
            title="❌ エラー発生",
            color=0xff0000  # 赤
        )
    
    def send_warning(self, message: str, details: Optional[Dict[str, Any]] = None) -> bool:
        """警告メッセージを送信（黄色）"""
        fields = []
        if details:
            for key, value in details.items():
                fields.append({
                    "name": key,
                    "value": str(value),
                    "inline": True
                })
        
        return self.send_message(
            content=message,
            title="⚠️ 警告",
            color=0xffff00,  # 黄
            fields=fields if fields else None
        )
    
    def send_info(self, message: str, details: Optional[Dict[str, Any]] = None) -> bool:
        """情報メッセージを送信（青色）"""
        fields = []
        if details:
            for key, value in details.items():
                fields.append({
                    "name": key,
                    "value": str(value),
                    "inline": True
                })
        
        return self.send_message(
            content=message,
            title="ℹ️ 情報",
            color=0x0099ff,  # 青
            fields=fields if fields else None
        )


def send_scraping_result(
    webhook_url: Optional[str],
    new_count: int,
    updated_count: int,
    new_alien_ids: list,
    images_downloaded: int = 0,
    errors: Optional[list] = None
) -> bool:
    """
    スクレイピング結果をDiscordに送信（簡易関数）
    
    Args:
        webhook_url: Discord Webhook URL
        new_count: 新規追加数
        updated_count: 更新数
        new_alien_ids: 新規エイリアンIDリスト
        images_downloaded: ダウンロードした画像数
        errors: エラーリスト
    
    Returns:
        成功した場合True
    """
    try:
        notifier = DiscordNotifier(webhook_url)
        
        if errors:
            # エラーがある場合
            error_summary = "\n".join([f"- {e}" for e in errors[:5]])  # 最大5件
            if len(errors) > 5:
                error_summary += f"\n（他{len(errors) - 5}件）"
            
            notifier.send_warning(
                "スクレイピング処理が完了しましたが、エラーが発生しました。",
                details={
                    "新規追加": new_count,
                    "更新": updated_count,
                    "画像ダウンロード": images_downloaded,
                    "エラー数": len(errors)
                }
            )
            # エラー詳細も別途送信
            notifier.send_error("エラー詳細", Exception(error_summary))
        else:
            # 成功の場合
            details = {
                "新規追加": new_count,
                "更新": updated_count,
                "画像ダウンロード": images_downloaded
            }
            if new_alien_ids:
                # 新規IDが多い場合は省略
                if len(new_alien_ids) <= 10:
                    id_list = ", ".join(map(str, new_alien_ids))
                else:
                    id_list = ", ".join(map(str, new_alien_ids[:10])) + f"（他{len(new_alien_ids) - 10}件）"
                details["新規エイリアンID"] = id_list
            
            notifier.send_success(
                "スクレイピング処理が正常に完了しました。",
                details=details
            )
        
        return True
    except Exception as e:
        print(f"Discord通知送信失敗: {e}")
        return False


if __name__ == '__main__':
    # テスト用
    import sys
    
    if len(sys.argv) < 2:
        print("使用方法: python discord_notifier.py <webhook_url>")
        print("または環境変数DISCORD_WEBHOOK_URLを設定")
        sys.exit(1)
    
    webhook_url = sys.argv[1] if len(sys.argv) > 1 else None
    
    try:
        notifier = DiscordNotifier(webhook_url)
        notifier.send_success("テストメッセージ", {"テスト項目": "テスト値"})
        print("通知送信成功")
    except Exception as e:
        print(f"エラー: {e}")
        sys.exit(1)

