
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from confluent_kafka import KafkaError, KafkaException, Consumer, DeserializingConsumer
from lms.djangoapps.arch_experiments.event_bus.course_events import (
    COURSE_EVENT_KEY_DESERIALIZER,
    COURSE_EVENT_VALUE_DESERIALIZER,
)

class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument('--topic', default="test-topic")

    def handle(self, *args, **options):
        consumer_settings = dict(settings.KAFKA_CONSUMER_CONF_BASE)
        consumer_settings.update({'key.deserializer': COURSE_EVENT_KEY_DESERIALIZER,
                                  'value.deserializer': COURSE_EVENT_VALUE_DESERIALIZER}

        )
        consumer = DeserializingConsumer(consumer_settings)
        print("Starting to consume messages...")
        try:
            consumer.subscribe([options["topic"]])

            while True:
                msg = consumer.poll(timeout=1.0)
               # print(msg)
                if msg is None: continue

                if msg.error():
                    if msg.error().code() == KafkaError._PARTITION_EOF:
                        # End of partition event
                        print('%% %s [%d] reached end at offset %d\n' %
                                         (msg.topic(), msg.partition(), msg.offset()))
                    elif msg.error():
                        raise KafkaException(msg.error())
                else:
                    course_key = msg.key().course_key
                    course_event = msg.value()
                    course = course_event.course
                    is_enroll = course_event.is_enroll
                    student = course_event.user_id

                    print(f"Course key: {course_key}")
                    print(f"{ 'Enrolled' if is_enroll else 'Unenrolled' } student {student} from {course.formatted_title()}")
        finally:
            # Close down consumer to commit final offsets.
            consumer.close()
