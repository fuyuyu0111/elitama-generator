"""
Discord Webhookを使用した通知機能
"""

import os
import json
import requests
from typing import Optional, Dict, Any, Set, List
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
    注意: この関数は後方互換性のため残していますが、新しいフォーマットにはsend_scraping_result_detailedを使用してください
    
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


def send_scraping_result_detailed(
    webhook_url: Optional[str],
    new_alien_names: Dict[int, str],
    updated_alien_names: Dict[int, str],
    changed_regular_skills: Set[str],
    changed_special_skills: Set[str],
    regular_analysis_results: Dict[str, List[Dict]],
    special_analysis_results: Dict[str, List[Dict]],
    images_downloaded: int = 0,
    error_info: Optional[Dict[str, str]] = None
) -> bool:
    """
    スクレイピング結果を詳細フォーマットでDiscordに送信（追加・更新・エラーを1つのメッセージに統合）
    
    Args:
        webhook_url: Discord Webhook URL
        new_alien_names: {alien_id: name} の辞書（追加エイリアン）
        updated_alien_names: {alien_id: name} の辞書（更新エイリアン）
        changed_regular_skills: 変更・追加された個性テキストセット
        changed_special_skills: 変更・追加された特技テキストセット
        regular_analysis_results: {skill_text: [効果情報]} の辞書（個性解析結果）
        special_analysis_results: {skill_text: [効果情報]} の辞書（特技解析結果）
        images_downloaded: ダウンロードした画像数
        error_info: {"step": "ステップ名", "message": "エラーメッセージ", "progress": "進捗状況"} の辞書
    
    Returns:
        成功した場合True
    """
    if not webhook_url:
        return False
    
    # 何もない場合は通知を送らない（解析結果だけでも通知を送る）
    if not new_alien_names and not updated_alien_names and not error_info and not changed_regular_skills and not changed_special_skills:
        return True
    
    try:
        notifier = DiscordNotifier(webhook_url)
        content_parts = []
        
        # 【追加】セクション
        if new_alien_names:
            add_content = "【追加】\n\n"
            
            # 追加エイリアン名
            alien_name_list = [f"{aid}: {name}" for aid, name in sorted(new_alien_names.items())]
            if len(alien_name_list) > 20:
                alien_name_list = alien_name_list[:20]
                alien_name_list.append(f"（他{len(new_alien_names) - 20}件）")
            add_content += "追加エイリアン名\n" + "\n".join(alien_name_list) + "\n\n"
            
            # 追加個性
            if changed_regular_skills:
                add_content += "追加個性\n"
                for skill_text in sorted(list(changed_regular_skills))[:10]:  # 最大10件
                    effects = regular_analysis_results.get(skill_text, [])
                    if effects:
                        effect_names = [e['effect_name'] for e in effects]
                        add_content += f"- {skill_text[:50]}...\n"
                        add_content += f"  効果: {', '.join(effect_names)}\n"
                    else:
                        add_content += f"- {skill_text[:50]}...\n"
                if len(changed_regular_skills) > 10:
                    add_content += f"（他{len(changed_regular_skills) - 10}件）\n"
                add_content += "\n"
            
            # 追加特技
            if changed_special_skills:
                add_content += "追加特技\n"
                for skill_text in sorted(list(changed_special_skills))[:10]:  # 最大10件
                    effects = special_analysis_results.get(skill_text, [])
                    if effects:
                        effect_names = [e['effect_name'] for e in effects]
                        add_content += f"- {skill_text[:50]}...\n"
                        add_content += f"  効果: {', '.join(effect_names)}\n"
                    else:
                        add_content += f"- {skill_text[:50]}...\n"
                if len(changed_special_skills) > 10:
                    add_content += f"（他{len(changed_special_skills) - 10}件）\n"
                add_content += "\n"
            
            # 追加エイリアン画像
            if images_downloaded > 0:
                add_content += f"追加エイリアン画像: {images_downloaded}件\n"
            
            content_parts.append(add_content)
        
        # 【更新】セクション
        if updated_alien_names:
            update_content = "【更新】\n\n"
            
            # 更新エイリアン名
            alien_name_list = [f"{aid}: {name}" for aid, name in sorted(updated_alien_names.items())]
            if len(alien_name_list) > 20:
                alien_name_list = alien_name_list[:20]
                alien_name_list.append(f"（他{len(updated_alien_names) - 20}件）")
            update_content += "更新エイリアン名\n" + "\n".join(alien_name_list) + "\n\n"
            
            # 更新個性（追加と同じ形式）
            if changed_regular_skills:
                update_content += "更新個性\n"
                for skill_text in sorted(list(changed_regular_skills))[:10]:  # 最大10件
                    effects = regular_analysis_results.get(skill_text, [])
                    if effects:
                        effect_names = [e['effect_name'] for e in effects]
                        update_content += f"- {skill_text[:50]}...\n"
                        update_content += f"  効果: {', '.join(effect_names)}\n"
                    else:
                        update_content += f"- {skill_text[:50]}...\n"
                if len(changed_regular_skills) > 10:
                    update_content += f"（他{len(changed_regular_skills) - 10}件）\n"
                update_content += "\n"
            
            # 更新特技（追加と同じ形式）
            if changed_special_skills:
                update_content += "更新特技\n"
                for skill_text in sorted(list(changed_special_skills))[:10]:  # 最大10件
                    effects = special_analysis_results.get(skill_text, [])
                    if effects:
                        effect_names = [e['effect_name'] for e in effects]
                        update_content += f"- {skill_text[:50]}...\n"
                        update_content += f"  効果: {', '.join(effect_names)}\n"
                    else:
                        update_content += f"- {skill_text[:50]}...\n"
                if len(changed_special_skills) > 10:
                    update_content += f"（他{len(changed_special_skills) - 10}件）\n"
                update_content += "\n"
            
            content_parts.append(update_content)
        
        # 【エラー】セクション（常に表示）
        if error_info:
            error_content = "【エラー】\n\n"
            error_content += f"発生箇所: {error_info.get('step', '不明')}\n"
            error_content += f"エラーメッセージ: {error_info.get('message', '不明')}\n"
            if error_info.get('progress'):
                error_content += f"\n進捗状況: {error_info.get('progress')}"
            content_parts.append(error_content)
        else:
            # エラーがない場合は「なし」と表示
            content_parts.append("【エラー】\n\nなし\n")
        
        # 【解析結果】セクション（新規追加・更新がない場合でも解析結果があれば表示）
        if (not new_alien_names and not updated_alien_names) and (changed_regular_skills or changed_special_skills):
            analysis_content = "【解析結果】\n\n"
            if changed_regular_skills:
                analysis_content += "解析された個性\n"
                for skill_text in sorted(list(changed_regular_skills))[:10]:  # 最大10件
                    effects = regular_analysis_results.get(skill_text, [])
                    if effects:
                        effect_names = [e['effect_name'] for e in effects]
                        analysis_content += f"- {skill_text[:50]}...\n"
                        analysis_content += f"  効果: {', '.join(effect_names)}\n"
                    else:
                        analysis_content += f"- {skill_text[:50]}...\n"
                if len(changed_regular_skills) > 10:
                    analysis_content += f"（他{len(changed_regular_skills) - 10}件）\n"
                analysis_content += "\n"
            
            if changed_special_skills:
                analysis_content += "解析された特技\n"
                for skill_text in sorted(list(changed_special_skills))[:10]:  # 最大10件
                    effects = special_analysis_results.get(skill_text, [])
                    if effects:
                        effect_names = [e['effect_name'] for e in effects]
                        analysis_content += f"- {skill_text[:50]}...\n"
                        analysis_content += f"  効果: {', '.join(effect_names)}\n"
                    else:
                        analysis_content += f"- {skill_text[:50]}...\n"
                if len(changed_special_skills) > 10:
                    analysis_content += f"（他{len(changed_special_skills) - 10}件）\n"
            
            content_parts.append(analysis_content)
        
        # すべてのセクションを結合して送信
        final_content = "\n\n".join(content_parts)
        notifier.send_message(
            content=final_content,
            timestamp=False
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

