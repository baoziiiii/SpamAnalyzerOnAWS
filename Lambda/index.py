import json
import boto3
import re
import base64
import os

import string
import sys
from hashlib import md5

if sys.version_info < (3,):
    maketrans = string.maketrans
else:
    maketrans = str.maketrans
    
def vectorize_sequences(sequences, vocabulary_length):
    results = [[0 for x in range(vocabulary_length)] for y in range(len(sequences))]
    for i, sequence in enumerate(sequences):
      for s in sequence:
        results[i][s] = 1
    return results

def one_hot_encode(messages, vocabulary_length):
    data = []
    for msg in messages:
        temp = one_hot(msg, vocabulary_length)
        data.append(temp)
    return data

def text_to_word_sequence(text,
                          filters='!"#$%&()*+,-./:;<=>?@[\\]^_`{|}~\t\n',
                          lower=True, split=" "):
    if lower:
        text = text.lower()

    if sys.version_info < (3,):
        if isinstance(text, unicode):
            translate_map = dict((ord(c), unicode(split)) for c in filters)
            text = text.translate(translate_map)
        elif len(split) == 1:
            translate_map = maketrans(filters, split * len(filters))
            text = text.translate(translate_map)
        else:
            for c in filters:
                text = text.replace(c, split)
    else:
        translate_dict = dict((c, split) for c in filters)
        translate_map = maketrans(translate_dict)
        text = text.translate(translate_map)

    seq = text.split(split)
    return [i for i in seq if i]

def one_hot(text, n,
            filters='!"#$%&()*+,-./:;<=>?@[\\]^_`{|}~\t\n',
            lower=True,
            split=' '):
    return hashing_trick(text, n,
                         hash_function='md5',
                         filters=filters,
                         lower=lower,
                         split=split)


def hashing_trick(text, n,hash_function=None,filters='!"#$%&()*+,-./:;<=>?@[\\]^_`{|}~\t\n',lower=True,split=' '):
    if hash_function is None:
        hash_function = hash
    elif hash_function == 'md5':
        hash_function = lambda w: int(md5(w.encode()).hexdigest(), 16)
    seq = text_to_word_sequence(text,filters=filters,lower=lower,split=split)
    return [int(hash_function(w) % (n - 1) + 1) for w in seq]

s3 = boto3.client('s3')
sgmaker = boto3.client('sagemaker-runtime')
ses = boto3.client('ses')
prediction_e1 = os.environ['e1']

def predict(emails):
    vl = 9013
    inputs = []
    for i in range(len(emails)):
        email = emails[i]['body']
        inputs.append(email.replace('\n','').replace('\r',''))
    print('[sagemaker_inputs]{}'.format(inputs))
    response = sgmaker.invoke_endpoint(
        EndpointName= prediction_e1,
        Body= json.dumps(vectorize_sequences(one_hot_encode(inputs, vl), vl)),
        ContentType='application/json'
    )['Body'].read()
    print('[sagemaker_response]{}'.format(response))
    return json.loads(response)
    
def reply_to_sender(emails,prediction):
    for i,email in enumerate(emails):
        CLASSIFICATION = 'SPAM' if prediction['predicted_label'][i][0] > 0 else 'HAM'
        CLASSIFICATION_CONFIDENCE_SCORE = prediction['predicted_probability'][i][0]
        EMAIL_RECEIVE_DATE = email['timestamp']
        EMAIL_SUBJECT = email['subject']
        reply = f"""<p>We received your email sent at {EMAIL_RECEIVE_DATE} with the subject {EMAIL_SUBJECT}.<br>
        Here is a 240 character sample of the email body:<br> {email['body']}<br>
        The email was categorized as <b>{CLASSIFICATION}</b> with a {CLASSIFICATION_CONFIDENCE_SCORE}% confidence.</p><br>
        """
        sender = email['sender']
        receiver = email['receiver']
        response = ses.send_email(
            Destination={'BccAddresses': [],'CcAddresses': [],'ToAddresses': [sender]},
            Message={
                'Body': {'Html': {'Charset': 'UTF-8','Data': '<div dir="ltr">{}</div>'.format(reply),},'Text': {'Charset': 'UTF-8','Data': reply,}},
                'Subject': {'Charset': 'UTF-8','Data': EMAIL_SUBJECT},
            },
            ReplyToAddresses=[sender],
            Source=receiver
        )
        print('ses_reponse:{}'.format(response))

def get_emails(records):
    emails = []
    for record in records:
        bucket = record['s3']['bucket']['name']
        key = record['s3']['object']['key']
        response = str(s3.get_object(
            Bucket=bucket,
            Key=key
        )['Body'].read().decode('utf-8'))
        sender = re.search(r'(?<=Return-Path: <)(.*?)(?=>)',response).group()
        receiver =  re.search(r'(?<=for )(.*?)(?=;)',response).group()
        subject = re.search(r'(?<=^Subject:)(.*?)(?=$)',response, re.MULTILINE).group()
        timestamp = re.search(r'(?<=^)(.*?)(?=\(UTC\))',response, re.MULTILINE).group()
        print("[sender]{}".format(sender))
        print("[receiver]{}".format(receiver))
        print("[subject]{}".format(subject))
        print("[timestamp]{}".format(timestamp))
        boundary = '--' + re.search(r'(?<=boundary=\")(.*?)(?=\")',response).group()
        body = re.search(r'(?<={0})(.*?)(?={0})'.format(boundary),response,re.DOTALL).group()[2:]
        encoding = re.search(r'(?<=Content-Transfer-Encoding: )(.*?)(?=\s)',body)
        message_body = body[body.index('\r\n\r\n'):]
        if encoding and encoding.group().strip().lower() == 'base64':
            print("[encoding]{}".format(encoding.group()))
            message_body = ''.join([s for s in message_body.splitlines() if s])
            message_body = base64.b64decode(message_body).decode('utf-8')
        print("[body]\n{}".format(message_body))
        emails.append({'sender':sender,'receiver':receiver,'subject':subject,'body':message_body, 'timestamp':timestamp})
    return emails
    
def lambda_handler(event, context):
    emails = get_emails(event['Records'])
    reply_to_sender(emails,predict(emails))

