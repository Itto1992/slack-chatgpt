import os
from logging import getLogger

import openai
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_bolt.context.say.say import Say

app = App(token=os.environ.get('SLACK_BOT_TOKEN'))
BOT_NAME = os.environ.get('BOT_NAME')
BOT_MENTION = f'<@{BOT_NAME}>'
SYSTEM_PROMPT = 'This is a conversation between a user and an helpful assistant. The assistant thinks step-by-step and gives useful advise to the user, with detailed explanation why he gives such advise.'  # noqa


def delete_prefix(message: str) -> str:
    """発話冒頭の余計な文字列を削除する。
    botにメンションした時の文字列はChatGPTのcompletionを実行する上で不要なため削除する。
    メンション後にスペース or 改行していた場合もトークンの無駄遣いになるので削除する。

    Args:
        message (str): slackに投稿された文字列。slack特有の表現が含まれる。

    Returns:
        str: 余分な文字列が削除された後の発話。
    """
    # botへのメンションを削除
    message = message.replace(BOT_MENTION, '')
    for prefix in [' ', '\n']:
        if message.startswith(prefix):
            message = message.replace(prefix, '')
            # 文頭から半角スペースと改行記号がなくなるまで反復して削除
            return delete_prefix(message)
    return message


def should_run_completion(event: dict) -> bool:
    """eventを見てChatGPTをトリガーするかを判定する。
    ChatGPTをトリガーしたいのは以下のタイミング
    - botにメンションした時
    - botにメンションした会話スレッドの中で新しく投稿があった時

    Args:
        event (dict): slackのイベント情報。

    Returns:
        bool: ChatGPTをトリガーすべきかどうか。Trueならトリガーする。
    """
    # 生成中のメッセージを消した時にcompletionがトリガーしてしまうのを防ぐ
    # NOTE: もっといい方法があるかもしれない
    if event.get('subtype') == 'message_changed':
        return False

    # 発話の行われたスレッドを取得する
    # チャネルに投稿された場合（スレッドでない場合）もスレッドとして取得している
    thread = app.client.conversations_replies(
        channel=event['channel'],
        ts=event.get('thread_ts', event['ts']),
    )
    origin_message = thread['messages'][0]['text']
    last_speaker = thread['messages'][-1]['user']
    # スレッドの最初がbotへのメンション、最後がユーザー発話の時にcompletionをトリガーする
    if BOT_MENTION in origin_message and last_speaker != BOT_NAME:
        return True
    else:
        return False


def create_messages(event: dict) -> list[dict]:
    """slackのスレッドをChatGPTのcompletionの形式に変換する。
    ChatGPTのcompletionは`list[dict]`の形式を要求するため、その形に変換する。

    Args:
        event (dict): slackのイベント情報。

    Returns:
        list[dict]: ChatGPTのcompletion用のメッセージ。
    """
    channel = event['channel']
    thread = app.client.conversations_replies(
        channel=channel,
        ts=event.get('thread_ts', event['ts']),
    )
    messages = []
    messages.append(dict(role='system', content=SYSTEM_PROMPT))
    for message in thread['messages']:
        messages.append(
            dict(
                role='assistant' if message['user'] == BOT_NAME else 'user',
                content=delete_prefix(message['text']),
            ),
        )
    return messages


@app.event('message')
def run_completion(event: dict, say: Say) -> None:
    """特定のポストが行われたタイミングでChatGPTを呼び出し、completion結果をツリーする。

    Args:
        event (dict): slackのイベント情報。
        say (slack_bolt.context.say.say.Say): skackにポストするためのクラス。
    """
    if not should_run_completion(event):
        return

    channel = event['channel']
    response = app.client.conversations_replies(channel=channel, ts=event['ts'])
    thread_ts = response['messages'][0]['ts']

    # 生成中であることを通知する。bot is typing...みたいなやつはAPIの機能として提供されていない
    tmp_response = app.client.chat_postMessage(
        channel=channel,
        thread_ts=thread_ts,
        text='生成なう',
    )

    messages = create_messages(event)
    completion = openai.ChatCompletion.create(
        model='gpt-3.5-turbo',
        messages=messages,
    )
    assistant_message = completion.choices[0].message.content

    # ChatGPTからのレスポンスが返ってきたら生成中のメッセージを消す
    app.client.chat_delete(
        channel=channel,
        ts=tmp_response['ts'],
    )

    say(assistant_message, channel=channel, thread_ts=thread_ts)


@app.event('app_mention')
def return_nothing(event, say):
    """app_mentionされた時のイベント定義。messageで必要挙動を定義しているのでここでは何もしない。"""
    # NOTE: これが最適な実装なのかは不明だが、定義しないとapp_mention時に未定義の警告が出たため作成している
    return


if __name__ == '__main__':
    SocketModeHandler(app, os.environ['SLACK_APP_TOKEN']).start()
